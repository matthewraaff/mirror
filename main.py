import os
import aiohttp
from aiohttp import web
import base64
import sqlite3
import string
import random
import datetime

UPLOADS_DIR = "uploads"
AUTH_USERNAME = "your_username"
AUTH_PASSWORD = "your_password"
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

if not os.path.exists(UPLOADS_DIR):
    os.makedirs(UPLOADS_DIR)

conn = sqlite3.connect('files.db')
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS file_metadata (
        filename TEXT,
        upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        time_to_live INTEGER,
        password TEXT,
        delete_after_download INTEGER
    )
''')
conn.commit()

def random_filename(original):
    return random.choice(string.ascii_letters) + str(random.randint(0, 100000)) + "." + original.split(".")[-1]

async def handle_upload(request):
    auth = request.headers.get('Authorization')
    if not auth or not check_auth(auth):
        return web.Response(text="Unauthorized", status=401)

    reader = await request.multipart()
    field = await reader.next()
    if field.name == 'file':
        # Retrieve the desired filename from the "filename" header, or use the original filename
        custom_filename = request.headers.get('filename', '')
        original_filename = field.filename
        if custom_filename:
            filename = custom_filename
        else:
            filename = original_filename

        filepath = os.path.join(UPLOADS_DIR, filename)

        # Check if the file already exists and handle duplicates
        if os.path.exists(filepath):
            filename = random_filename(original_filename)
            filepath = os.path.join(UPLOADS_DIR, filename)

        # Retrieve additional options from headers
        time_to_live = int(request.headers.get('time', 24 * 60 * 60))  # Default: 24 hours
        password = request.headers.get('password', '')
        delete_after_download = int(request.headers.get('delete', 0))  # Default: 0 (no deletion)

        size = 0
        with open(filepath, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    return web.Response(text="File too large - Only files up to 100MB are allowed", status=413)
                f.write(chunk)

        upload_time = datetime.datetime.now()

        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO file_metadata (filename, upload_time, time_to_live, password, delete_after_download)
            VALUES (?, ?, ?, ?, ?)
        ''', (filename, upload_time, time_to_live, password, delete_after_download))
        conn.commit()

        return web.Response(text=f"File {filename} uploaded successfully")

    return web.Response(text="Invalid request")



async def directory_listing(request):
    # Curl example:
    # curl -u your_username:your_password -H "path=folder" http://mirror.raaff.dev/list
    auth = request.headers.get('Authorization')
    if not auth or not check_auth(auth):
        return web.Response(text="Unauthorized", status=401)

    path = request.query.get('path', '')
    if not path:
        path = "."
    path = os.path.join(UPLOADS_DIR, path)

    if not os.path.exists(path):
        return web.Response(text="Directory not found", status=404)

    # add folders but with a / at the end
    folders = [f"{f}/" for f in os.listdir(path) if os.path.isdir(os.path.join(path, f))]
    files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]

    # Show metadata for files
    cursor = conn.cursor()
    cursor.execute('''
        SELECT filename, time_to_live, password, delete_after_download
        FROM file_metadata
    ''')
    metadata = cursor.fetchall()
    for filename, time_to_live, password, delete_after_download in metadata:
        if filename in files:
            files[files.index(filename)] += f" (ttl: {time_to_live}, password: {password}, delete: {delete_after_download})"
        elif filename in folders:
            folders[folders.index(filename)] += f" (ttl: {time_to_live}, password: {password}, delete: {delete_after_download})"
        else:
            files.append(f"{filename} (ttl: {time_to_live}, password: {password}, delete: {delete_after_download})")

    return web.Response(text="\n".join(sorted(folders + files)))


def check_auth(auth_header):
    try:
        auth_type, auth_info = auth_header.split()
        if auth_type.lower() == "basic":
            username_password = base64.b64decode(auth_info).decode('utf-8')
            username, password = username_password.split(':')
            return username == AUTH_USERNAME and password == AUTH_PASSWORD
    except Exception:
        pass
    return False


async def handle_download(request):
    filename = request.match_info.get('filename')
    filepath = os.path.join(UPLOADS_DIR, filename)

    # Check if the file exists
    if not os.path.exists(filepath):
        return web.Response(text="File not found", status=404)

    # Check if the file has TTL or maximum download limits
    cursor = conn.cursor()
    cursor.execute('''
        SELECT upload_time, time_to_live, delete_after_download
        FROM file_metadata
        WHERE filename = ?
    ''', (filename,))
    file_info = cursor.fetchone()

    if not file_info:
        return web.Response(text="File not found in metadata", status=404)

    upload_time_str, time_to_live, delete_after_download = file_info
    current_time = datetime.datetime.now()

    # Convert the upload_time_str to a datetime object
    upload_time = datetime.datetime.strptime(upload_time_str, '%Y-%m-%d %H:%M:%S.%f')

    # Check TTL (Its in hours)
    if time_to_live > 0:
        time_to_live = datetime.timedelta(hours=time_to_live)
        if current_time - upload_time > time_to_live:
            os.remove(filepath)
            cursor.execute('DELETE FROM file_metadata WHERE filename = ?', (filename,))
            conn.commit()
            return web.Response(text="File has expired. If you are the webmaster, you may be able to salvage it.", status=410)

    # Check maximum downloads
    if delete_after_download > 0:
        delete_after_download -= 1
        if delete_after_download == 0:
            os.remove(filepath)
            cursor.execute('DELETE FROM file_metadata WHERE filename = ?', (filename,))
        else:
            cursor.execute('UPDATE file_metadata SET delete_after_download = ? WHERE filename = ?', (delete_after_download, filename))
        conn.commit()

    return web.FileResponse(filepath)


app = web.Application()
app.router.add_post('/upload', handle_upload)
app.router.add_get('/list', directory_listing)
app.router.add_get('/{filename}', handle_download)
app.router.add_get('/', lambda _: web.Response(text="Welcome to the raaff.dev web mirror\nhttps://github.com/matthewraaff/mirror"))

web.run_app(app)
