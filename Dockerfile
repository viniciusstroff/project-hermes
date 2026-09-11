FROM python:3.12-slim

ARG USER=project-hermes
ARG HOME=/home/$USER

# Adiciona repositório do PostgreSQL para versões antigas (método moderno)
RUN apt-get update \
 && apt-get install --yes wget gnupg lsb-release \
 && mkdir -p /etc/apt/keyrings \
 && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install --yes postgresql-client-9.6 \
 && apt-get clean

# instala libs como root (melhor)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt \
&& pip install debugpy

# cria usuário
RUN useradd --create-home --shell /bin/bash $USER

USER $USER
WORKDIR $HOME

COPY --chown=$USER:$USER . .

CMD ["tail", "-f", "/dev/null"]