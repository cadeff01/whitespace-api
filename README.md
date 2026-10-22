# Lumen App Service basic Python Flask sample

This repository contains a minimal Python Flask web app used by Lumen's Web App
create template experience. Lumen provisions the Azure resources with its own ARM
template and configures App Service source control to deploy the application code
from this repository.

## What's included

- `app.py` - Flask application entry point.
- `requirements.txt` - Python dependencies used by App Service build/deploy.
- `templates/` and `static/` - HTML templates and static assets for the sample app.
- `infra/` and `azure.yaml` - Azure Developer CLI/Bicep examples copied from the
  upstream sample for reference and local experimentation.

## Lumen deployment behavior

When this sample is selected in Lumen:

1. Lumen collects basic Web App values such as app name, subscription, resource
   group, and region.
2. Lumen deploys Azure resources through the portal create ARM template.
3. The ARM deployment configures `Microsoft.Web/sites/sourcecontrols` so App
   Service pulls this repository and deploys the Flask app code.

The infrastructure files in this repository are not used by Lumen create. They
are included only as reference material. Editing `infra/` or `azure.yaml` will
not change resources that Lumen already created.

## Run locally

```shell
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

On Windows PowerShell, activate the virtual environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Attribution

This sample is adapted from
[`Azure-Samples/msdocs-python-flask-webapp-quickstart`](https://github.com/Azure-Samples/msdocs-python-flask-webapp-quickstart),
the sample application for the Azure App Service Python Flask quickstart.
