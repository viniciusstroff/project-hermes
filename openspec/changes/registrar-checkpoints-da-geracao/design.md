## Context

Depois da `paralelizar-geracao-de-tarefas-independentes`:

- O trabalho pesado de uma migração está em tarefas com nome único: os métodos com `@task` e as cópias compatíveis (`copy:<entidade>`).
- O processo principal junta o resultado de cada tarefa ao arquivo da fase na ordem de junção. Com 1 worker, a tarefa escreve direto no output; com mais, num segmento juntado por `include_file()`.
- O `ArtifactManager` já aceita um `timestamp` para reabrir uma pasta de execução. O manifesto guarda o status (`running`, `success`, `failed`).
- O `relatorio-geracao.json` guarda as unidades, as fases e as tarefas.

Uma geração que falha deixa o arquivo da fase com as tarefas juntadas até a falha. Com 1 worker, o arquivo pode ter também parte da tarefa seguinte.

## Goals / Non-Goals

**Goals:**

- Retomar uma geração que falhou na mesma pasta, sem gerar de novo as tarefas concluídas.
- Recusar a retomada quando o resultado poderia ficar diferente de uma geração limpa.
- Manter os artefatos de uma geração retomada iguais aos de uma geração limpa.

**Non-Goals:**

- Não criar checkpoint na aplicação. Ela continua tudo ou nada. Checkpoint na aplicação é o pré-requisito para a aplicação paralela e fica para outra change.
- Não retomar com o output direto ou híbrido. A transação no destino já foi desfeita na falha.
- Não registrar checkpoint dentro de uma tarefa, por lote. Uma tarefa interrompida recomeça do zero.
- Não detectar atualizações de linhas na origem entre as tentativas.

## Decisions

1. A tarefa é a unidade do checkpoint. O nome é a identidade.

   O nome é único na classe e não muda com condições ou com a ordem de execução. Posição e rótulo da unidade não serviriam: rótulos se repetem (duas unidades gravam `phones` na AcmeEdu) e condições mudam a ordem. O que fica fora de tarefas é gerado de novo na retomada.

2. Checkpoint por posição no arquivo da fase, sem cópia.

   Depois de cada tarefa juntada, o `FileOutput` descarrega o buffer e faz `fsync`. O checkpoint registra:

   - o nome, a fase e a sequência da tarefa;
   - as posições de início e fim do trecho no arquivo da fase;
   - o sha256 do trecho, calculado durante a escrita;
   - a contagem de linhas da origem de cada unidade da tarefa;
   - um hash do código do método (`inspect.getsource`).

   O `checkpoint.json` fica na raiz da execução, gravado num arquivo temporário e trocado com `os.replace`. Um `flock` na pasta impede duas retomadas ao mesmo tempo.

   Alternativa considerada: guardar o segmento de cada tarefa até o fim e montar os arquivos das fases só no sucesso. Rejeitada porque grava duas vezes a fase `03`, que num cliente grande ocupa gigabytes. Isso também dobra o pico de disco. Com posições, os artefatos são os mesmos e nada é copiado.

3. Retomada.

   `python3 Migracao.py <cliente> --resume <timestamp>`:

   - Reabre `runs/<cliente>/<timestamp>/`. Aceita status `failed` e também `running`, que fica quando o processo morre. Recusa `success`.
   - Carrega os mapas de referência, fecha as leituras do destino e confere a impressão digital (decisão 4).
   - Para cada fase, acha a última tarefa confirmada em sequência, confere o sha256 dos trechos até ela e trunca o arquivo nesse ponto.
   - Roda os hooks de novo em modo de pulo até o corte: operações fora de tarefas são renderizadas e conferidas pelo tamanho, sem gravar; tarefas antes do corte não rodam. As métricas delas vêm do checkpoint. Efeitos como `suspended_constraints` continuam sendo registrados, porque a fase final precisa deles.
   - Uma divergência de tamanho, de nome ou de sha256 recusa a retomada com uma mensagem que diz onde.
   - Depois do corte, a geração segue normal, em sequência ou em paralelo.

   Com mais de 1 worker, tarefas podem terminar fora de ordem. Uma tarefa que terminou mas não foi juntada tem o segmento completo e o sha256 registrados. Na retomada, ela entra sem rodar de novo.

4. Impressão digital.

   O checkpoint guarda o que, se mudar, deixaria a geração retomada diferente de uma geração limpa. Uma diferença recusa a retomada:

   - versão do core: o HEAD do git e um hash das mudanças não commitadas em `core/`;
   - adapters e versões dos servidores;
   - conexões sem senha e um hash da URI, que o `describe()` omite;
   - limite de debug;
   - tipo e formato do output;
   - hash do conteúdo dos mapas de referência carregados;
   - catálogo do destino usado para renderizar o `SuspendConstraints`.

   O número de workers fica de fora: ele não muda os artefatos.

5. Código do cliente e `--rerun`.

   O caso mais comum de retomada é corrigir uma transformação e rodar de novo. Por isso mudança no script do cliente não recusa a retomada inteira. Ela é conferida por tarefa, pelo hash do código do método. Uma tarefa concluída cujo código mudou gera um aviso com o nome dela e não roda de novo sozinha. `--rerun <nome>` corta a fase no início daquela tarefa. Ela e tudo o que vem depois na fase rodam de novo.

6. Mudança na origem.

   As restrições do destino ficam suspensas durante a carga. Uma origem que mudou entre as tentativas geraria linhas órfãs sem erro nenhum. Na retomada, as unidades das tarefas concluídas são contadas de novo na origem. Uma contagem diferente recusa a retomada. A documentação avisa que a contagem não pega atualização de linhas. A premissa continua sendo a origem parada.

7. Fim da execução.

   No sucesso, os segmentos que sobrarem são apagados e o `checkpoint.json` fica com status `success`, para auditoria. O relatório da geração marca as tarefas retomadas e lista as tentativas, com início e fim de cada uma.

   O runner passa a tratar `BaseException`. Num Ctrl+C, ele grava `failed` no manifesto e o checkpoint antes de relançar a interrupção.

## Risks / Trade-offs

- Script do cliente com efeito colateral fora de tarefas -> o modo de pulo roda os hooks de novo. Um hook que grava em disco ou em outro sistema repete o efeito. Documentar que hooks só emitem operações.
- Origem atualizada sem mudar a contagem -> passa pela conferência. A premissa de origem parada continua valendo.
- `fsync` depois de cada tarefa -> custo pequeno, uma vez por tarefa.
- O modo de pulo confere o tamanho das operações fora de tarefas, não o conteúdo -> uma mudança que mantenha o tamanho passa. Essas operações são comentários e limpezas, geradas de novo de qualquer forma.

## Migration Plan

1. Arquivar a `paralelizar-geracao-de-tarefas-independentes`.
2. Criar o registro, a trava e a impressão digital, gravando o checkpoint numa geração normal.
3. Criar a retomada com corte e modo de pulo, primeiro com 1 worker.
4. Levar a retomada às tarefas paralelas e aos segmentos não juntados.
5. Criar `--rerun`, a conferência da origem e o tratamento do Ctrl+C.
6. Provar com a AcmeEdu e documentar.

Rollback: reverter os commits. Sem `--resume`, a geração só grava o `checkpoint.json` a mais.

## Open Questions

- Vale um modo que aceita a mudança de contagem da origem, para quem sabe que a mudança não afeta as tarefas concluídas?
- A retomada com o output híbrido vale a pena? Ela precisaria executar no destino o prefixo do arquivo antes de seguir.
