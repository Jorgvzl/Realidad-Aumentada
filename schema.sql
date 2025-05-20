-- Elimina la tabla 'videos' si ya existe, para evitar errores al reiniciar el esquema.
DROP TABLE IF EXISTS videos;

-- Crea la tabla 'videos' con las columnas necesarias.
CREATE TABLE videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- Identificador único para cada video, se autoincrementa.
    title TEXT,                            -- Título del video, puede ser opcional (NULL).
    filename TEXT NOT NULL UNIQUE,         -- Nombre del archivo físico guardado en el servidor. Debe ser único.
    url_slug TEXT NOT NULL UNIQUE,         -- Identificador único para la URL del video (ej: 'video_x2fg7s'). Debe ser único.
    mimetype TEXT NOT NULL,                -- Tipo MIME del archivo de video (ej: 'video/mp4', 'video/webm').
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- Fecha y hora de creación del registro, se establece automáticamente.
);

-- Opcionalmente, puedes agregar índices para mejorar el rendimiento de las búsquedas si esperas tener muchos videos.
CREATE INDEX idx_url_slug ON videos (url_slug);