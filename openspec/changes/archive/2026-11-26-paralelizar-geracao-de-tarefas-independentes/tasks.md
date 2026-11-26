## 1. Prerequisites

- [x] 1.1 Implement and archive `preservar-quebras-de-linha-nos-artefatos`.

## 2. Task Declaration

- [x] 2.1 Create `core/tasks.py` with the `task(name=None, sequence=1)` decorator, a declaration counter and the discovery of the tasks of a class, refusing repeated names.
- [x] 2.2 Add `BaseMigration.run_tasks()`, running the tasks in merge order, and make the default `run_inserts()` call it.
- [x] 2.3 Run the tasks in sequence directly into the output with 1 worker, keeping today's artifacts.

## 3. Task Context and Segments

- [x] 3.1 Keep the source `ConnectionConfig` objects in `MigrationContext` and connect a source adapter per worker from them.
- [x] 3.2 Create the task context in `core/parallel.py`: own source connections, segment writer, private metrics collector, shared reference maps, debug limit, configuration and target renderer.
- [x] 3.3 Make `BaseMigration.context` resolve to the task context inside a worker, through a `ContextVar` in threads.
- [x] 3.4 Refuse `Clear`, `SuspendConstraints` and `ResetIdentity` inside a task.
- [x] 3.5 Record the owner process and thread of each source adapter and raise when another one uses it.
- [x] 3.6 Write segments under `tarefas/<NN>_<phase>/` without newline translation and merge them through `include_file()` in merge order as soon as every earlier task finished, removing each segment.

## 4. Executors

- [x] 4.1 Create the thread executor with a cancel event checked in `engine.insert()`, closing the worker connections after the join.
- [x] 4.2 Create the process executor: N `fork` processes created at once from the main thread, a shared counter for the tasks and a queue for the results; children ignore SIGINT, never close inherited adapters and exit with `os._exit`; stdout and output buffers flushed before the fork.
- [x] 4.3 Refuse the process mode outside Linux and when any adapter is MongoDB, before connecting.
- [x] 4.4 Cancel the remaining tasks on the first failure and on Ctrl+C, failing the run with the task name.

## 5. Single Writer

- [x] 5.1 Send log lines, events, progress, warnings and unit metrics from the workers to the main process through a channel; write logs, terminal and metrics only in the main process.
- [x] 5.2 Give each task a private `GenerationMetrics`, add `Unit.from_dict` and let the main collector adopt task units in merge order.
- [x] 5.3 Replace the progress footer with `k de n tarefas` lines while tasks run, and deduplicate `warn_once` in the main process.

## 6. Compatible Entities

- [x] 6.1 Run each `CopyEntity` as a task `copy:<entity>` with more than one worker, keeping row by row copies before the native copy for each source.
- [x] 6.2 Run the native copy as a task whose temporary file is its segment.

## 7. Command Line and Records

- [x] 7.1 Add `--workers` and `--worker-mode` to `Migracao.py` and pass them to the context.
- [x] 7.2 Record workers and worker mode in the manifest, and the tasks in the generation report.
- [x] 7.3 With direct and hybrid outputs, cancel the running tasks when a merged command fails under `stop`.

## 8. Client and Documentation

- [x] 8.1 Replace `run_inserts()` in `AcmeEdu/AcmeEdu.py` with `@task` on the six `insert_*` methods, in the same order and sequence 1.
- [x] 8.2 Document tasks, sequences, workers and the rules for client scripts in `docs/novo-cliente.md`, `docs/conversao-clientes.md`, `docs/arquitetura.md` and `docs/execucao.md`.
- [x] 8.3 Mark item 12 of `docs/melhorias-futuras.md` with what was implemented and what remains.

## 9. Verification

- [x] 9.1 Add tests for the decorator: default name, repeated name, sequence order and merge order.
- [x] 9.2 Add tests with fake sources: same artifact bytes with 1 worker, 4 threads and 4 processes; no segment left; same generation report units.
- [x] 9.3 Add tests for failures: a failing task cancels the others, a forbidden operation in a task, an engine used by another worker, process mode refused with MongoDB.
- [x] 9.4 Add tests for the hybrid output with workers: the same commands run on a fake session as with 1 worker.
- [x] 9.5 Run AcmeEdu with 1, 2 and 4 workers in both modes against PostgreSQL, compare the artifacts with the sequential run, run `conferir.py` and record the generation times in the design.
- [x] 9.6 Run `python3 -m unittest discover -s tests` and `openspec validate paralelizar-geracao-de-tarefas-independentes --strict`.
