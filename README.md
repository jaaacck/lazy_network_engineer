# Lazy Network Engineer

A hierarchical project management system for network engineers, using markdown files with YAML frontmatter for data storage.

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
- `DATA_ROOT` - Where to store project data (defaults to ./data)

## Features

- **Hierarchical project structure** - Projects contain Epics, Epics contain Tasks, Tasks contain Subtasks
- **Markdown support** - Rich text formatting with markdown
- **File-based storage** - All data stored as markdown files with YAML frontmatter
- **Progress tracking** - Automatic progress calculation for epics based on task completion
- **Updates system** - Track progress updates on tasks and subtasks
- **Security** - Path traversal protection, XSS prevention, environment-based configuration

## Project Structure

```
project_manager/
├── pm/                          # Main app
│   ├── views.py                # View functions
│   ├── utils.py                # Shared utilities (load/save, validation)
│   ├── templates/              # HTML templates
│   └── templatetags/           # Custom template filters
├── data/                       # Project data (markdown files)
│   └── projects/
│       ├── project-xxx.md
│       └── project-xxx/
│           └── epics/
│               ├── epic-xxx.md
│               └── epic-xxx/
│                   └── tasks/
│                       ├── task-xxx.md
│                       └── task-xxx/
│                           └── subtasks/
│                               └── subtask-xxx.md
├── logs/                       # Application logs
├── requirements.txt            # Python dependencies
├── .env.example               # Environment configuration template
└── manage.py                  # Django management script
```

## Security Features

✅ **Path Traversal Protection** - All entity IDs validated with regex patterns
✅ **XSS Prevention** - HTML sanitization with bleach
✅ **Environment Variables** - Secrets stored in environment, not code
✅ **Proper Logging** - Security events logged to file
✅ **Input Validation** - All user inputs validated before processing

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
- **PyYAML** - YAML parsing
- **Markdown** - Text formatting
- **Bleach** - HTML sanitization
- **BeautifulSoup4** - HTML parsing

## License

[Your License Here]

## Contributing

[Contributing Guidelines Here]
