import sqlite3
import os
import uuid # Para generar nombres de archivo únicos o slugs
from flask import Flask, render_template, request, redirect, url_for, g, send_from_directory
from flask import session, flash # session y flash ya están, redirect y url_for estaban duplicados en comentario
import qrcode # Importar la librería qrcode
import io # Para manejar la imagen en memoria
import base64 # Para codificar la imagen en base64
import zipfile # Para manejar archivos ZIP
import shutil # Para operaciones de archivos como eliminar directorios
from datetime import datetime # Para convertir strings de fecha a objetos datetime

app = Flask(__name__)
app.config['DATABASE'] = 'database.db'
app.config['UPLOAD_FOLDER'] = os.path.join('uploads', 'videos')
app.config['MODELS_UPLOAD_FOLDER'] = os.path.join('uploads', 'models') # Carpeta para modelos 3D
app.config['ALLOWED_EXTENSIONS'] = {'mp4', 'webm', 'ogg'}
app.config['ALLOWED_MODEL_EXTENSIONS'] = {'zip'} # Extensiones permitidas para modelos 3D
app.secret_key = '4dm1nUnefa'  # Cambia esto a una clave secreta real y más segura

# Crear las carpetas de subidas si no existen
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])
if not os.path.exists(app.config['MODELS_UPLOAD_FOLDER']):
    os.makedirs(app.config['MODELS_UPLOAD_FOLDER'])

# --- Funciones de Base de Datos ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db_path = app.config['DATABASE']
        db = g._database = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
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

        DROP TABLE IF EXISTS models3d;
        CREATE TABLE models3d (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            slug TEXT NOT NULL UNIQUE, -- Nombre de la carpeta en uploads/models
            obj_filename TEXT NOT NULL, -- Nombre del archivo .obj dentro de la carpeta slug
            mtl_filename TEXT NOT NULL, -- Nombre del archivo .mtl dentro de la carpeta slug
            qr_code_data_uri TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        db.cursor().executescript(schema_sql)
        db.commit()
        print("Base de datos inicializada con tablas 'videos' y 'models3d'.")

def check_init_db():
    db_path = app.config['DATABASE']
    if not os.path.exists(db_path):
        print(f"Base de datos no encontrada en '{db_path}'. Inicializando...")
        init_db()
    else:
        print(f"Base de datos encontrada en '{db_path}'.")

check_init_db()

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def allowed_model_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_MODEL_EXTENSIONS']

# --- Rutas para Videos ---

# Ruta para mostrar el panel de administración y el formulario de subida
@app.route('/', methods=['GET']) # Simplificado a GET, ya que POST se maneja en add_video
def admin_panel():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, title, filename, url_slug, qr_code_data_uri, created_at FROM videos ORDER BY created_at DESC")
    videos_raw = cursor.fetchall()
    
    processed_videos = []
    for row_data in videos_raw:
        video_item = dict(row_data)
        raw_timestamp = video_item.get('created_at')
        if isinstance(raw_timestamp, str):
            try:
                video_item['created_at'] = datetime.strptime(raw_timestamp, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    video_item['created_at'] = datetime.strptime(raw_timestamp, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError:
                    print(f"Advertencia: No se pudo parsear la fecha '{raw_timestamp}' para el video ID {video_item.get('id')}.")
                    video_item['created_at'] = None # O alguna fecha por defecto o dejar como string
        elif not isinstance(raw_timestamp, datetime) and raw_timestamp is not None:
             video_item['created_at'] = None # Manejar si no es ni string ni datetime
        processed_videos.append(video_item)

    return render_template('admin_panel.html', videos=processed_videos, config=app.config)

# Nueva ruta para manejar la subida de videos (el endpoint será 'add_video')
@app.route('/admin/add_video', methods=['POST'])
def add_video():
    if not session.get('logged_in'):
        flash('Debes iniciar sesión para realizar esta acción.', 'warning')
        return redirect(url_for('login'))

    db = get_db()
    title = request.form.get('title')
    video_file = request.files.get('video_file')

    if not video_file or video_file.filename == '':
        flash('Ningún archivo de video seleccionado.', 'error')
        return redirect(url_for('admin_panel'))

    if video_file and allowed_file(video_file.filename):
        original_filename = video_file.filename
        extension = original_filename.rsplit('.', 1)[1].lower()
        
        unique_id = str(uuid.uuid4())
        new_filename = f"{unique_id}.{extension}"
        url_slug = f"video_{unique_id}"

        filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
        
        try:
            video_file.save(filepath)
            mimetype = video_file.mimetype

            # Generar URL y QR DESPUÉS de guardar el archivo y ANTES de hacer commit a la BD
            # para que si algo falla, no queden registros huérfanos.
            video_url_for_qr = url_for('play_video', slug=url_slug, _external=True)
            url_render = modificar_video_url(video_url_for_qr)
            
            qr_img = qrcode.make(url_render)
            buffered = io.BytesIO()
            qr_img.save(buffered, format="PNG")
            qr_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            qr_code_data_uri = f"data:image/png;base64,{qr_base64}"

            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO videos (title, filename, url_slug, mimetype, qr_code_data_uri) VALUES (?, ?, ?, ?, ?)",
                (title if title else original_filename, new_filename, url_slug, mimetype, qr_code_data_uri)
            )
            db.commit()
            flash('Video agregado exitosamente!', 'success')
        except sqlite3.IntegrityError as e:
            if os.path.exists(filepath): # Si falla la BD, eliminar el archivo subido
                os.remove(filepath)
            print(f"Error de integridad al insertar en la BD (videos): {e}")
            flash(f"Error al guardar el video debido a un conflicto de datos: {e}", 'error')
        except Exception as e: # Captura otras posibles excepciones durante save, QR, etc.
            if os.path.exists(filepath): # Si falla algo, eliminar el archivo subido
                os.remove(filepath)
            print(f"Error al procesar la subida del video: {e}")
            flash(f"Error al procesar la subida del video: {e}", 'error')
        
        return redirect(url_for('admin_panel'))
    else:
        flash('Tipo de archivo de video no permitido.', 'error')
        return redirect(url_for('admin_panel'))


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
                               video_title=video_data['title'],
                               slug=slug)
    else:
        flash('Video no encontrado.', 'error')
        return "Video no encontrado", 404

@app.route('/uploads/videos/<filename>')
def uploaded_file(filename):
    response = send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Cross-Origin-Resource-Policy', 'cross-origin')
    return response

@app.route('/delete_video/<int:video_id>', methods=['POST'])
def delete_video(video_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
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
            flash('Video eliminado exitosamente.', 'success')
        except Exception as e:
            print(f"Error al eliminar el video {video_id} o su archivo: {e}")
            flash(f"Error al eliminar el video: {e}", 'error')
    else:
        flash('Video no encontrado para eliminar.', 'error')
    return redirect(url_for('admin_panel'))

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
        flash('Video no encontrado para vista RA.', 'error')
        return "Video no encontrado", 404

# --- Rutas para Modelos 3D ---
@app.route('/admin_models', methods=['GET', 'POST'])
def admin_models_panel():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    db = get_db()

    if request.method == 'POST':
        title = request.form.get('title')
        model_zip_file = request.files.get('model_zip_file')

        if not model_zip_file or model_zip_file.filename == '':
            flash('Ningún archivo de modelo seleccionado.', 'error')
            return redirect(request.url) # request.url aquí está bien, es la misma página

        if model_zip_file and allowed_model_file(model_zip_file.filename):
            original_filename = model_zip_file.filename
            slug = f"model_{str(uuid.uuid4())}"
            model_upload_path = os.path.join(app.config['MODELS_UPLOAD_FOLDER'], slug)
            
            if not os.path.exists(model_upload_path):
                os.makedirs(model_upload_path)

            zip_filepath = os.path.join(model_upload_path, original_filename)
            
            obj_filename = None
            mtl_filename = None

            try:
                model_zip_file.save(zip_filepath)
            
                with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
                    for member in zip_ref.namelist():
                        if member.startswith('/') or '..' in member:
                            raise ValueError("El archivo ZIP contiene rutas inválidas.")
                    zip_ref.extractall(model_upload_path)
                
                for item in os.listdir(model_upload_path):
                    if item.lower().endswith('.obj') and obj_filename is None:
                        obj_filename = item
                    elif item.lower().endswith('.mtl') and mtl_filename is None:
                        mtl_filename = item
                
                if not obj_filename or not mtl_filename:
                    shutil.rmtree(model_upload_path) 
                    flash('Archivo .OBJ o .MTL no encontrado en el archivo ZIP.', 'error')
                    return redirect(request.url)

                os.remove(zip_filepath) 

                ar_model_url = url_for('view_model_in_ar', slug=slug, _external=True)
                url_render_model = modificar_video_url(ar_model_url)
                
                qr_img_model = qrcode.make(url_render_model)
                buffered_model = io.BytesIO()
                qr_img_model.save(buffered_model, format="PNG")
                qr_base64_model = base64.b64encode(buffered_model.getvalue()).decode('utf-8')
                qr_code_data_uri_model = f"data:image/png;base64,{qr_base64_model}"

                cursor = db.cursor()
                cursor.execute(
                    "INSERT INTO models3d (title, slug, obj_filename, mtl_filename, qr_code_data_uri) VALUES (?, ?, ?, ?, ?)",
                    (title if title else "Modelo sin título", slug, obj_filename, mtl_filename, qr_code_data_uri_model)
                )
                db.commit()
                flash('Modelo 3D subido exitosamente!', 'success')

            except zipfile.BadZipFile:
                if os.path.exists(model_upload_path): shutil.rmtree(model_upload_path)
                flash('Archivo ZIP inválido.', 'error')
                return redirect(request.url)
            except ValueError as ve: 
                if os.path.exists(model_upload_path): shutil.rmtree(model_upload_path)
                flash(str(ve), 'error')
                return redirect(request.url)
            except Exception as e:
                if os.path.exists(model_upload_path): shutil.rmtree(model_upload_path)
                flash(f'Ocurrió un error al procesar el modelo: {str(e)}', 'error')
                print(f"Error al procesar la subida del modelo: {e}")
                return redirect(request.url)
            
            return redirect(url_for('admin_models_panel'))
        else:
            flash('Tipo de archivo inválido para modelo. Por favor, sube un archivo ZIP.', 'error')
            return redirect(request.url)

    # Lógica GET para admin_models_panel
    cursor = db.cursor()
    cursor.execute("SELECT id, title, slug, obj_filename, mtl_filename, qr_code_data_uri, created_at FROM models3d ORDER BY created_at DESC")
    models_raw = cursor.fetchall()
    
    processed_models = []
    for row_data in models_raw:
        model_item = dict(row_data) 
        raw_timestamp = model_item.get('created_at')

        if isinstance(raw_timestamp, str):
            try:
                model_item['created_at'] = datetime.strptime(raw_timestamp, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    model_item['created_at'] = datetime.strptime(raw_timestamp, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError:
                    print(f"Advertencia: No se pudo parsear la fecha '{raw_timestamp}' para el modelo ID {model_item.get('id')}.")
                    model_item['created_at'] = None 
        elif not isinstance(raw_timestamp, datetime) and raw_timestamp is not None:
            print(f"Advertencia: 'created_at' para el modelo ID {model_item.get('id')} es de tipo inesperado: {type(raw_timestamp)}. Valor: {raw_timestamp}")
            model_item['created_at'] = None

        processed_models.append(model_item)
    
    return render_template('admin_models.html', models=processed_models, config=app.config)


@app.route('/view_model_in_ar/<string:slug>')
def view_model_in_ar(slug):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT title, slug, obj_filename, mtl_filename FROM models3d WHERE slug = ?", (slug,))
    model_data = cursor.fetchone()

    if model_data:
        return render_template('view_model_in_ar.html',
                               model_title=model_data['title'],
                               model_slug=model_data['slug'],
                               obj_filename=model_data['obj_filename'],
                               mtl_filename=model_data['mtl_filename'])
    else:
        flash('Modelo 3D no encontrado.', 'error')
        return "Modelo 3D no encontrado", 404

@app.route('/uploads/models/<string:slug>/<path:filename>')
def serve_model_file(slug, filename):
    model_dir = os.path.join(app.config['MODELS_UPLOAD_FOLDER'], slug)
    if ".." in filename or filename.startswith("/"):
        return "Acceso denegado", 403
    return send_from_directory(model_dir, filename)

@app.route('/delete_model/<int:model_id>', methods=['POST'])
def delete_model(model_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT slug FROM models3d WHERE id = ?", (model_id,))
    model_data = cursor.fetchone()

    if model_data:
        model_slug = model_data['slug']
        model_folder_path = os.path.join(app.config['MODELS_UPLOAD_FOLDER'], model_slug)
        try:
            if os.path.exists(model_folder_path):
                shutil.rmtree(model_folder_path)
            
            cursor.execute("DELETE FROM models3d WHERE id = ?", (model_id,))
            db.commit()
            flash('Modelo 3D eliminado exitosamente.', 'success')
        except Exception as e:
            print(f"Error al eliminar el modelo 3D {model_id} o su carpeta: {e}")
            flash(f'Error al eliminar el modelo: {str(e)}', 'error')
    else:
        flash('Modelo 3D no encontrado para eliminar.', 'error')
            
    return redirect(url_for('admin_models_panel'))

# --- Rutas de Autenticación y Utilidades ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == '4dm1n': 
            session['logged_in'] = True
            flash('Inicio de sesión exitoso!', 'success')
            return redirect(url_for('admin_panel'))
        else:
            flash('Credenciales incorrectas. Inténtalo de nuevo.', 'error')
            return redirect(url_for('login')) # Redirige a login para reintentar
    # Si es GET, o si el login falla y se redirige aquí sin POST (aunque el flujo actual no hace eso)
    if session.get('logged_in'): # Si ya está logueado y visita /login, redirigir al panel
        return redirect(url_for('admin_panel'))
    return render_template('login.html')


@app.route('/logout', methods=['POST']) 
def logout():
    session.pop('logged_in', None)
    flash('Has cerrado sesión.', 'info')
    return redirect(url_for('login'))

def modificar_video_url(video_url):
    base_render_url = os.environ.get('RENDER_EXTERNAL_URL', 'https://realidad-aumentada-sa1f.onrender.com')
    path_part = ""
    if video_url.startswith('http://') or video_url.startswith('https://'):
        try:
            path_part = '/' + video_url.split('/', 3)[3]
        except IndexError:
            path_part = '/' # Si es solo el dominio base
    else: # Si es una ruta relativa como /video/slug
        path_part = video_url if video_url.startswith('/') else '/' + video_url

    if base_render_url.endswith('/') and path_part.startswith('/'):
        nueva_url = base_render_url + path_part[1:]
    elif not base_render_url.endswith('/') and not path_part.startswith('/'):
        nueva_url = base_render_url + '/' + path_part
    else:
        nueva_url = base_render_url + path_part
        
    return nueva_url


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get("PORT", 5000)),
        debug=False # Desactiva debug=True en producción real
    )
