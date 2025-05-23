import sqlite3
import os
import uuid # Para generar nombres de archivo únicos o slugs
from flask import Flask, render_template, request, redirect, url_for, g, send_from_directory
from flask import session, redirect, url_for, flash
import qrcode # Importar la librería qrcode
import io # Para manejar la imagen en memoria
import base64 # Para codificar la imagen en base64

app = Flask(__name__)
app.config['DATABASE'] = 'database.db'
# Es buena práctica definir UPLOAD_FOLDER usando el directorio de la instancia de Render si usas Discos Persistentes
# Por ahora, lo mantenemos relativo. Ejemplo con Render Disks:
# app.config['UPLOAD_FOLDER'] = os.path.join(os.environ.get('RENDER_INSTANCE_DIR', '.'), 'uploads', 'videos')
# app.config['DATABASE'] = os.path.join(os.environ.get('RENDER_INSTANCE_DIR', '.'), 'database.db')
app.config['UPLOAD_FOLDER'] = os.path.join('uploads', 'videos')
app.config['ALLOWED_EXTENSIONS'] = {'mp4', 'webm', 'ogg'}
app.secret_key = '4dm1nUnefa'  # Cambia esto a una clave secreta real


# Crear la carpeta de subidas si no existe
# Esto se ejecutará cuando el módulo se cargue por primera vez
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# --- Funciones de Base de Datos ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        # Asegúrate de que la ruta de la base de datos es la correcta
        db_path = app.config['DATABASE']
        db = g._database = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row # Para acceder a las columnas por nombre
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    # Esta función se llamará si la base de datos no existe.
    with app.app_context():
        db = get_db()
        schema_sql = """
        DROP TABLE IF EXISTS videos;
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            filename TEXT NOT NULL UNIQUE,
            url_slug TEXT NOT NULL UNIQUE,
            mimetype TEXT NOT NULL,
            qr_code_data_uri TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        db.cursor().executescript(schema_sql)
        db.commit()
        print("Base de datos inicializada.")

def check_init_db():
    # Comprueba si la base de datos existe, si no, la inicializa.
    # Esto es crucial para entornos como Render donde el `if __name__ == '__main__':` no se ejecuta de la misma manera.
    db_path = app.config['DATABASE']
    if not os.path.exists(db_path):
        print(f"Base de datos no encontrada en '{db_path}'. Inicializando...")
        init_db() # No necesitas app.app_context() aquí porque init_db ya lo maneja.
    else:
        print(f"Base de datos encontrada en '{db_path}'.")

# Llama a check_init_db() cuando la aplicación se configura.
# Esto asegura que la base de datos se inicialice antes de que se maneje la primera solicitud si no existe.
check_init_db()


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/', methods=['GET', 'POST'])
def admin_panel():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    db = get_db()
   
    if request.method == 'POST':
        title = request.form.get('title')
        video_file = request.files.get('video_file')

        if not video_file or video_file.filename == '':
            # Considera añadir un mensaje flash para el usuario
            return redirect(request.url)

        if video_file and allowed_file(video_file.filename):
            original_filename = video_file.filename
            extension = original_filename.rsplit('.', 1)[1].lower()
            
            unique_id = str(uuid.uuid4())
            # Limpia el nombre del archivo para crear un slug más amigable si es necesario
            # Por ahora, usamos el unique_id directamente para el nombre del archivo también
            new_filename = f"{unique_id}.{extension}"
            url_slug = f"video_{unique_id}" # Slug para la URL

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
            video_file.save(filepath)
            mimetype = video_file.mimetype

            # Generar URL completa para el código QR
            # _external=True es importante para que la URL sea absoluta
            video_url = url_for('play_video', slug=url_slug, _external=True)

            # Generar Código QR
            url_render = modificar_video_url(video_url)
            qr_img = qrcode.make(url_render)
            buffered = io.BytesIO()
            qr_img.save(buffered, format="PNG")
            qr_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            qr_code_data_uri = f"data:image/png;base64,{qr_base64}"

            try:
                cursor = db.cursor()
                cursor.execute(
                    "INSERT INTO videos (title, filename, url_slug, mimetype, qr_code_data_uri) VALUES (?, ?, ?, ?, ?)",
                    (title if title else original_filename, new_filename, url_slug, mimetype, qr_code_data_uri)
                )
                db.commit()
            except sqlite3.IntegrityError as e:
                # Esto podría ocurrir si el filename o url_slug no son únicos, aunque uuid debería prevenirlo.
                # Es buena práctica eliminar el archivo subido si la inserción en BD falla.
                os.remove(filepath)
                print(f"Error de integridad al insertar en la BD: {e}")
                # Considera añadir un mensaje flash para el usuario
                pass 
            return redirect(url_for('admin_panel'))
        else:
            # Considera añadir un mensaje flash para el usuario sobre el tipo de archivo no permitido
            return redirect(request.url)

    cursor = db.cursor()
    cursor.execute("SELECT id, title, filename, url_slug, qr_code_data_uri, created_at FROM videos ORDER BY created_at DESC")
    videos = cursor.fetchall()
    return render_template('admin_panel.html', videos=videos, config=app.config)

@app.route('/video/<string:slug>')
def play_video(slug):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT title, filename, mimetype, url_slug FROM videos WHERE url_slug = ?", (slug,))
    video_data = cursor.fetchone()

    if video_data:
        
        return render_template('video_player.html', 
                           video_filename=video_data['filename'], 
                           video_mimetype=video_data['mimetype'], 
                           video_title=video_data['title'],  # Agregar el título aquí
                           slug=slug)
    

    else:
        return "Video no encontrado", 404



@app.route('/uploads/videos/<filename>')
def uploaded_file(filename):
    response = send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Cross-Origin-Resource-Policy', 'cross-origin')
    return response

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # Verificar las credenciales
        if username == 'admin' and password == '4dm1n':
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))
        else:
            flash('Credenciales incorrectas. Inténtalo de nuevo.')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/delete_video/<int:video_id>', methods=['POST'])
def delete_video(video_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT filename FROM videos WHERE id = ?", (video_id,))
    video_data = cursor.fetchone()

    if video_data:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], video_data['filename'])
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
            
            cursor.execute("DELETE FROM videos WHERE id = ?", (video_id,))
            db.commit()
        except Exception as e:
            print(f"Error al eliminar el video {video_id} o su archivo: {e}")
            # Considera añadir un mensaje flash para el usuario
            pass # Evita que la app crashee, pero loguea el error
            
    return redirect(url_for('admin_panel'))

def modificar_video_url(video_url):
    """
    Lee una URL de video, elimina el contenido hasta los 3 primeros '/',
    y luego agrega el prefijo 'https://realidad-aumentada-sa1f.onrender.com/'.

    Args:
        video_url (str): La URL del video original.

    Returns:
        str: La URL modificada.
    """
    contador_slashes = 0
    indice_tercer_slash = -1

    for i, char in enumerate(video_url):
        if char == '/':
            contador_slashes += 1
            if contador_slashes == 3:
                indice_tercer_slash = i
                break

    if indice_tercer_slash != -1:
        ruta_video = video_url[indice_tercer_slash + 1:]
        nueva_url = f"https://realidad-aumentada-sa1f.onrender.com/{ruta_video}"
        return nueva_url
    else:
        # Si no se encontraron 3 '/', devolver la URL original o manejar el error
        return "No se encontraron suficientes '/' en la URL."

@app.route('/view_in_ar/<string:slug>')
def view_in_ar(slug):
       db = get_db()
       cursor = db.cursor()
       cursor.execute("SELECT title, filename FROM videos WHERE url_slug = ?", (slug,))
       video_data = cursor.fetchone()

       if video_data:
           return render_template('view_in_ar.html', 
                                  video_filename=video_data['filename'], 
                                  video_title=video_data['title'])
       else:
           return "Video no encontrado", 404
   


# El bloque if __name__ == '__main__': se usa para el desarrollo local.
# Gunicorn (o el servidor WSGI que use Render) no ejecutará esto directamente.
# En su lugar, importará la instancia 'app' de este módulo.
if __name__ == '__main__':
    # La inicialización de la BD ya se maneja con check_init_db() al inicio del script.
    # app.run() es solo para desarrollo local.
    # Render usará Gunicorn con un comando como: gunicorn app:app
    app.run(
        host='0.0.0.0', # Escucha en todas las interfaces de red
        port=int(os.environ.get("PORT", 5000)), # Render puede establecer la variable PORT
        debug=False, # Desactiva debug=True en producción real o usa una variable de entorno

    )
