# Updates Monitoring

Status: early development.

Service that can monitoring updates for your home lab and perform remote update.

Support integration with:
- Proxmox
- Docker

Send data to:
- InfluxDB
- Telegram

## Development

### Local

```
python3 -m venv dist
source dist/bin/activate 
pip install -r ./app/requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### Docker

```
docker compose -f compose-dev.yaml up --no-deps --build
```

To create admion user

```
docker compose exec server python manage.py createsuperuser
```

## Production

```
docker compose up --no-deps --build
```
