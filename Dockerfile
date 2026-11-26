FROM python:3.12-slim

ARG USER=project-hermes
ARG HOME=/home/$USER

# Cliente PostgreSQL (pg_dump e psql) de uma versão principal, só para quem liga
# a cópia nativa (SOURCE_<NOME>_NATIVE_COPY=pg_dump) ou aplica com o rodar2.sh.
# O pg_dump precisa ser da versão do servidor de origem ou mais novo. Vazio,
# a imagem fica sem cliente. Ex.: docker compose build --build-arg PG_CLIENT_VERSION=17
ARG PG_CLIENT_VERSION=

RUN if [ -n "$PG_CLIENT_VERSION" ]; then \
        apt-get update \
     && apt-get install --yes --no-install-recommends wget gnupg lsb-release ca-certificates \
     && mkdir -p /etc/apt/keyrings \
     && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg \
     && echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
     && apt-get update \
     && apt-get install --yes --no-install-recommends "postgresql-client-$PG_CLIENT_VERSION" \
     && apt-get clean \
     && rm -rf /var/lib/apt/lists/*; \
    fi

# A imagem do Hermes instala o core e os drivers de todos os adapters.
COPY requirements.txt /tmp/requirements.txt
COPY requirements/ /tmp/requirements/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt $(printf -- '-r %s ' /tmp/requirements/*.txt) \
 && pip install --no-cache-dir debugpy

# cria usuário
RUN useradd --create-home --shell /bin/bash $USER

USER $USER
WORKDIR $HOME

COPY --chown=$USER:$USER . .

CMD ["tail", "-f", "/dev/null"]
