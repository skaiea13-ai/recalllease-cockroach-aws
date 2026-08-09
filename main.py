from mangum import Mangum

from recalllease.api import create_app

app = create_app()
handler = Mangum(app, lifespan="off")
