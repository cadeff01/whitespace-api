import os

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from werkzeug.exceptions import HTTPException

from providers.sam import sam_api
from providers.errors import APIError

app = Flask(__name__)
app.config.from_mapping(
    AZURE_KEY_VAULT_ENDPOINT=os.environ.get(
        'AZURE_KEY_VAULT_ENDPOINT', 'https://whitespacekv.vault.azure.net/'
    ),
    SAM_API_KEY_SECRET_NAME=os.environ.get('SAM_API_KEY_SECRET_NAME', 'sam-gov-api-key'),
    LOCAL_DEVELOPMENT=os.environ.get('LOCAL_DEVELOPMENT') == 'true',
)
app.register_blueprint(sam_api, url_prefix='/api/sam')


@app.before_request
def authenticate_api():
    if not request.path.startswith('/api/'):
        return None
    if app.config['LOCAL_DEVELOPMENT'] and not os.environ.get('WEBSITE_SITE_NAME'):
        return None
    # App Service Authentication strips external identity headers and injects its own.
    # The backend must only be reachable through that platform authentication layer.
    if (not os.environ.get('WEBSITE_SITE_NAME')
            or request.headers.get('X-MS-CLIENT-PRINCIPAL-IDP') != 'aad'
            or not request.headers.get('X-MS-CLIENT-PRINCIPAL-ID')):
        raise APIError(401, 'unauthorized', 'Microsoft Entra authentication is required.')
    return None


@app.errorhandler(APIError)
def handle_api_error(error):
    if error.status >= 500 or error.code == 'upstream_rate_limited':
        app.logger.warning('API request failed: %s', error.code)
    response = jsonify(error={'code': error.code, 'message': error.message})
    response.status_code = error.status
    if error.status == 401:
        response.headers['WWW-Authenticate'] = 'Bearer'
    return response


@app.errorhandler(HTTPException)
def handle_http_error(error):
    if request.path.startswith('/api/'):
        return jsonify(error={'code': error.name.lower().replace(' ', '_'),
                              'message': error.description}), error.code
    return error


@app.get('/health')
def health():
    return jsonify(status='ok')


@app.get('/openapi.json')
def openapi():
    return send_from_directory(app.root_path, 'openapi.json')


@app.route('/')
def index():
   print('Request for index page received')
   return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/hello', methods=['POST'])
def hello():
   name = request.form.get('name')

   if name:
       print('Request for hello page received with name=%s' % name)
       return render_template('hello.html', name = name)
   else:
       print('Request for hello page received with no name or blank name -- redirecting')
       return redirect(url_for('index'))


if __name__ == '__main__':
   app.run()
