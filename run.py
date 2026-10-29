"""Utvecklingsserver. I produktion: gunicorn 'consens:create_app()'."""
from consens import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
