from .main import create_app
app = create_app(auto_seed=True)   # re-seeds on every start: demo data is reproducible
