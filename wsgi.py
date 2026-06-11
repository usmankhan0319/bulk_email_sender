from a2wsgi import ASGIMiddleware
from app import app

application = ASGIMiddleware(app)
