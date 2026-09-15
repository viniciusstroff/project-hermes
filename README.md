# Project Hermes
 Aplicação cli para gerar as queries de migração de dados do banco v1 para a v2


**Hermes** é o projeto responsável pela execução e orquestração da migração de bancos de dados da plataforma, garantindo a integridade, rastreabilidade e consistência dos dados durante o processo de transição.

---

## 🎯 Objetivo

O objetivo principal do Hermes é prover um mecanismo confiável e reprodutível para transportar schemas e/ou massas de dados entre fontes, minimizando *downtime* e prevenindo perda de dados.

---

## 🛠️ Tecnologias & Arquitetura

- **Interface:** CLI / Pipeline de Execução
- **Linguagem/Stack:** *(Adicione aqui a linguagem, ex: Go, Node.js, Python, Rust)*
- **Bancos Suportados:** *(Adicione aqui, ex: PostgreSQL, MySQL, MongoDB)*

---

## 🚀 Como Executar

### Pré-requisitos
- Docker & Docker Compose *(opcional, se aplicável)*
- Variáveis de ambiente configuradas no arquivo `.env`

### Configuração de Ambiente
Crie um arquivo `.env` baseado no modelo disponível:




## 🏛️ Por que "Hermes"?

Na mitologia grega, **Hermes** é o deus mensageiro, encarregado de transitar livremente entre diferentes reinos para conduzir e entregar mensagens e almas com absoluta precisão e velocidade.

O projeto recebe este nome porque desempenha exatamente esse papel no ecossistema de dados: atua como o **guia confiável que transporta as informações do seu estado de origem ao seu novo destino**, garantindo velocidade na entrega, integridade no caminho e zero perda de dados durante a travessia.

Para debugar:

docker exec -it project-hermes python3 -m debugpy --listen 0.0.0.0:5678 --wait-for-client Migracao.py
