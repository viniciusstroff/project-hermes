## 1. Prerequisites

- [ ] 1.1 Implement and archive `preservar-quebras-de-linha-nos-artefatos`.

## 2. Task Declaration

- [ ] 2.1 Create `core/tasks.py` with the `task(name=None, sequence=1)` decorator, a declaration counter and the discovery of the tasks of a class, refusing repeated names.
- [ ] 2.2 Add `BaseMigration.run_tasks()`, running the tasks in merge order, and make the default `run_inserts()` call it.
- [ ] 2.3 Run the tasks in sequence directly into the output with 1 worker, keeping today's artifacts.

## 3. Task Context and Segments

- [ ] 3.1 Keep the source `ConnectionConfig` objects in `MigrationContext` and connect a source adapter per worker from them.
- [ ] 3.2 Create the task context in `core/parallel.py`: own source connections, segment writer, private metrics collector, shared reference maps, debug limit, configuration and target renderer.
- [ ] 3.3 Make `BaseMigration.context` resolve to the task context inside a worker, through a `ContextVar` in threads.
- [ ] 3.4 Refuse `Clear`, `SuspendConstraints` and `ResetIdentity` inside a task.
- [ ] 3.5 Record the owner process and thread of each source adapter and raise when another one uses it.
- [ ] 3.6 Write segments under `tarefas/<NN>_<phase>/` without newline translation and merge them through `include_file()` in merge order as soon as every earlier task finished, removing each segment.

## 4. Executors

- [ ] 4.1 Create the thread executor with a cancel event checked in `engine.insert()`, closing the worker connections after the join.
- [ ] 4.2 Create the process executor: N `fork` processes created at once from the main thread, a shared counter for the tasks and a queue for the results; children ignore SIGINT, never close inherited adapters and exit with `os._exit`; stdout and output buffers flushed before the fork.
- [ ] 4.3 Refuse the process mode outside Linux and when any adapter is MongoDB, before connecting.
- [ ] 4.4 Cancel the remaining tasks on the first failure and on Ctrl+C, failing the run with the task name.

## 5. Single Writer

- [ ] 5.1 Send log lines, events, progress, warnings and unit metrics from the workers to the main process through a channel; write logs, terminal and metrics only in the main process.
- [ ] 5.2 Make the engine hold its own `Unit`, add `Unit.from_dict` and let `GenerationMetrics` adopt task units in merge order.
- [ ] 5.3 Replace the progress footer with `k de n tarefas` lines while tasks run, and deduplicate `warn_once` in the main process.

## 6. Compatible Entities

- [ ] 6.1 Run each `CopyEntity` as a task `copy:<entity>` with more than one worker, keeping row by row copies before the native copy for each source.
- [ ] 6.2 Run the native copy as a task whose temporary file is its segment.

## 7. Command Line and Records

- [ ] 7.1 Add `--workers` and `--worker-mode` to `Migracao.py` and pass them to the context.
- [ ] 7.2 Record workers and worker mode in the manifest, and the tasks in the generation report.
- [ ] 7.3 With direct and hybrid outputs, cancel the running tasks when a merged command fails under `stop`.

## 8. Client and Documentation

- [ ] 8.1 Replace `run_inserts()` in `AcmeEdu/AcmeEdu.py` with `@task` on the six `insert_*` methods, in the same order and sequence 1.
- [ ] 8.2 Document tasks, sequences, workers and the rules for client scripts in `docs/novo-cliente.md`, `docs/conversao-clientes.md`, `docs/arquitetura.md` and `docs/execucao.md`.
- [ ] 8.3 Mark item 12 of `docs/melhorias-futuras.md` with what was implemented and what remains.

## 9. Verification

- [ ] 9.1 Add tests for the decorator: default name, repeated name, sequence order and merge order.
- [ ] 9.2 Add tests with fake sources: same artifact bytes with 1 worker, 4 threads and 4 processes; no segment left; same generation report units.
- [ ] 9.3 Add tests for failures: a failing task cancels the others, a forbidden operation in a task, an engine used by another worker, process mode refused with MongoDB.
- [ ] 9.4 Add tests for the hybrid output with workers: the same commands run on a fake session as with 1 worker.
- [ ] 9.5 Run AcmeEdu with 1, 2 and 4 workers in both modes against PostgreSQL, compare the artifacts with the sequential run, run `conferir.py` and record the generation times in the design.
- [ ] 9.6 Run `python3 -m unittest discover -s tests` and `openspec validate paralelizar-geracao-de-tarefas-independentes --strict`.
