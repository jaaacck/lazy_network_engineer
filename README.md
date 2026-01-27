# Lazy Network Engineer

101% Vibe Coded

---

A hierarchical project management system for network engineers, using SQLite database for data storage.

## Hierarchy

Projects → Epics → Tasks → Subtasks

## Quick Start

### 1. Setup Virtual Environment
```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # Linux/Mac
# OR
venv\Scripts\activate  # Windows
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Initialize Database
```bash
mkdir -p logs
python manage.py migrate

# If migrating from file-based storage, run:
# python manage.py migrate_to_sqlite --backup
```

### 4. Run Server
```bash
python manage.py runserver
```

Visit http://localhost:8000 in your browser.

## Configuration (Optional)

Create a `.env` file for custom configuration:

```bash
cp .env.example .env
```

Edit `.env` to set:
- `SECRET_KEY` - Django secret key (generate a new one for production)
- `DEBUG` - Set to False for production
- `ALLOWED_HOSTS` - Your domain names
- `DATA_ROOT` - Where to store uploads and other files (defaults to ./data)

## Features

- **Hierarchical project structure** - Projects contain Epics, Epics contain Tasks, Tasks contain Subtasks
- **Markdown support** - Rich text formatting with markdown
- **SQLite database** - All data stored in SQLite database for reliability and performance
- **Progress tracking** - Automatic progress calculation for epics based on task completion
- **Updates system** - Track progress updates on tasks and subtasks
- **Security** - Input validation, XSS prevention, environment-based configuration

## Project Structure

```
project_manager/
├── pm/                          # Main app
│   ├── models.py               # Django models (Entity, Update)
│   ├── views.py                # View functions
│   ├── utils.py                # Shared utilities (validation, helpers)
│   ├── storage/                # Storage layer
│   │   └── index_storage.py   # SQLite operations and search index
│   ├── templates/              # HTML templates
│   └── templatetags/           # Custom template filters
├── db.sqlite3                  # SQLite database (all entity data)
├── data/                       # Uploads and other files
│   └── uploads/                # User-uploaded images
├── logs/                       # Application logs
├── requirements.txt            # Python dependencies
├── .env.example               # Environment configuration template
└── manage.py                  # Django management script
```

## Security Features

✅ **Input Validation** - All entity IDs validated with regex patterns
✅ **XSS Prevention** - HTML sanitization with bleach
✅ **Environment Variables** - Secrets stored in environment, not code
✅ **Proper Logging** - Security events logged to file
✅ **SQL Injection Protection** - Django ORM provides parameterized queries

## Testing

Run the test suite:
```bash
python test_improvements.py
```

Run Django tests:
```bash
python manage.py test pm
```

## Documentation

- `CLAUDE.md` - Developer guide for working with this codebase
- `IMPROVEMENTS.md` - Detailed list of all improvements made
- `UPGRADE_NOTES.md` - Quick upgrade guide
- `.env.example` - Configuration options

## Production Deployment

For production use:

1. Generate a strong SECRET_KEY
2. Set `DEBUG=False`
3. Configure `ALLOWED_HOSTS`
4. Use a production WSGI server (gunicorn, uwsgi)
5. Set up proper file permissions
6. Configure log rotation

See `IMPROVEMENTS.md` for detailed production deployment guide.

## Technology Stack

- **Django 6.0.1** - Web framework
- **SQLite** - Database (via Django ORM)
- **Markdown** - Text formatting
- **Bleach** - HTML sanitization
- **BeautifulSoup4** - HTML parsing
- **PyYAML** - Used only for migration from file-based storage (can be removed after migration)
