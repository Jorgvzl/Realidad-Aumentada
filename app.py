import sqlite3
import os
import uuid # Para generar nombres de archivo únicos o slugs
from flask import Flask, render_template, request, redirect, url_for, g, send_from_directory
import qrcode # Importar la librería qrcode
import io # Para manejar la imagen en memoria
import base64 # Para codificar la imagen en base64

app = Flask(__name__)
app.config['DATABASE'] = 'database.db'
app.config['UPLOAD_FOLDER'] = os.path.join('uploads', 'videos') # Ruta donde se guardarán los videos
app.config['ALLOWED_EXTENSIONS'] = {'mp4', 'webm', 'ogg'} # Formatos permitidos

# Crear la carpeta de subidas si no existe
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# --- Funciones de Base de Datos ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row # Para acceder a las columnas por nombre
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        # Actualiza el schema.sql para incluir qr_code_data_uri
        # Si ya tienes datos, necesitarás migrar tu tabla o reiniciarla.
        schema_sql = """
        DROP TABLE IF EXISTS videos;
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            filename TEXT NOT NULL UNIQUE,
            url_slug TEXT NOT NULL UNIQUE,
            mimetype TEXT NOT NULL,
            qr_code_data_uri TEXT, -- Nuevo campo para el QR
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        db.cursor().executescript(schema_sql)
        db.commit()

# Comprobación para inicializar la BD si no existe (ejecutar solo una vez o proteger)
# Es recomendable manejar la inicialización de la BD de forma más controlada en producción.
# Por ejemplo, con un comando CLI de Flask.
# from app import init_db
# init_db()


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/', methods=['GET', 'POST'])
def admin_panel():
    db = get_db()
    if request.method == 'POST':
        title = request.form.get('title')
        video_file = request.files.get('video_file')

        if not video_file or video_file.filename == '':
            return redirect(request.url)

        if video_file and allowed_file(video_file.filename):
            original_filename = video_file.filename
            extension = original_filename.rsplit('.', 1)[1].lower()
            
            unique_id = str(uuid.uuid4())
            new_filename = f"{unique_id}.{extension}"
            url_slug = f"video_{unique_id}"

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
            video_file.save(filepath)
            mimetype = video_file.mimetype

            # Generar URL completa para el código QR
            video_url = url_for('play_video', slug=url_slug, _external=True)

            # Generar Código QR
            qr_img = qrcode.make(video_url)
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
            except sqlite3.IntegrityError:
                os.remove(filepath)
                pass # Considera añadir un mensaje de error
            return redirect(url_for('admin_panel'))
        else:
            return redirect(request.url) # Considera añadir un mensaje de error

    cursor = db.cursor()
    # Asegúrate de seleccionar el nuevo campo qr_code_data_uri
    cursor.execute("SELECT id, title, filename, url_slug, qr_code_data_uri, created_at FROM videos ORDER BY created_at DESC")
    videos = cursor.fetchall()
    return render_template('admin_panel.html', videos=videos, config=app.config)

@app.route('/video/<string:slug>')
def play_video(slug):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT filename, mimetype FROM videos WHERE url_slug = ?", (slug,))
    video_data = cursor.fetchone()

    if video_data:
        return render_template('video_player.html', video_filename=video_data['filename'], video_mimetype=video_data['mimetype'], slug=slug)
    else:
        return "Video no encontrado", 404

@app.route('/uploads/videos/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


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
            print(f"Error al eliminar: {e}")
            pass
            
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    # Crear la base de datos si no existe la primera vez
    # Es más robusto usar un script de inicialización o migraciones para esto
    db_path = app.config['DATABASE']
    if not os.path.exists(db_path):
        print(f"Base de datos no encontrada en '{db_path}'. Inicializando...")
        # Asegúrate de que el contexto de la aplicación esté activo para init_db
        with app.app_context():
             init_db()
        print("Base de datos inicializada.")
        
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True,
        #ssl_context=('localhost+3.pem', 'localhost+3-key.pem')  # HTTPS habilitado

    )
