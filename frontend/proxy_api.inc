# Relais vers l'API (inclus par nginx.conf).
proxy_pass http://api:8000;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
proxy_set_header Connection "";
proxy_read_timeout 60s;
proxy_buffering off;
# Le backend pose ses propres en-têtes de sécurité (CSP API, no-store) : on ne les écrase pas.
proxy_hide_header X-Powered-By;
