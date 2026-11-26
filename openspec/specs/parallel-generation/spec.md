# parallel-generation Specification

## Purpose
Define how a migration script declares tasks with the `task` decorator and how the generation runs the tasks of the same sequence in threads or processes, each in its own task context, merging their segments in merge order so the artifacts match the sequential generation.
## Requirements
### Requirement: Tasks are declared with a decorator
A migration script SHALL declare tasks by decorating its methods with `@task(name=None, sequence=1)` from `core.tasks`. The name SHALL default to the method name and SHALL be unique in the class. The default `BaseMigration.run_inserts()` SHALL run the declared tasks, and `run_tasks()` SHALL run them from a `run_inserts()` written by the client.

#### Scenario: Name defaults to the method name
- **WHEN** a method `insert_courses` is decorated with `@task()`
- **THEN** the task is named `insert_courses` with sequence 1

#### Scenario: Repeated name
- **WHEN** two methods of the same class declare the task name `students`
- **THEN** the class is refused with an error naming both methods

#### Scenario: Client without tasks
- **WHEN** a client overrides `run_inserts()` and declares no task
- **THEN** the generation runs as before

### Requirement: Sequences order the tasks
Tasks with the same sequence SHALL be allowed to run at the same time. A task SHALL start only after every task of a lower sequence finished. The merge order SHALL be the sequence and, within it, the declaration order in the class.

#### Scenario: Dependent task
- **WHEN** `classes` has sequence 2 and `subjects` has sequence 1
- **THEN** `classes` starts only after `subjects` finished

#### Scenario: Merge order
- **WHEN** `teachers` finishes before `students` and both have sequence 1, with `students` declared first
- **THEN** the commands of `students` come before the commands of `teachers` in the phase artifact

### Requirement: Parallel generation keeps the sequential artifacts
With more than one worker, each task SHALL write its commands to its own segment, and the main process SHALL merge the segments into the phase through `include_file()` in the merge order, as soon as every earlier task finished, removing each segment after the merge. With one worker, tasks SHALL run in sequence directly into the output.

#### Scenario: Same artifacts as the sequential run
- **WHEN** a migration whose source queries have `order_by` runs with 1 worker and again with 4 workers
- **THEN** the phase artifacts of both runs have the same bytes

#### Scenario: Segments are removed
- **WHEN** a parallel generation finishes
- **THEN** no segment is left under `tarefas/`

### Requirement: Workers are threads or processes
`Migracao.py` SHALL accept `--workers N`, default 1, and `--worker-mode thread|process`, default `thread`. The process mode SHALL use `fork`, SHALL be offered only on Linux and SHALL be refused before any connection when any adapter of the run is MongoDB.

#### Scenario: Process mode with MongoDB
- **WHEN** `Migracao.py` runs with `--workers 4 --worker-mode process` and the target adapter is MongoDB
- **THEN** it exits with an error naming the MongoDB adapter before connecting

#### Scenario: Process mode outside Linux
- **WHEN** `--worker-mode process` is used on a platform other than Linux
- **THEN** it exits with an error suggesting the thread mode

### Requirement: Each task runs in its own task context
Inside a worker, the migration context SHALL be a task context with its own source connections, a segment writer as output and a private metrics collector, sharing the reference maps, the debug limit, the configuration and the target adapter for rendering. A task SHALL emit only `Insert`, `Comment` and `Native` operations. A source adapter used by a process or thread other than its owner SHALL raise an error.

#### Scenario: Own source connection
- **WHEN** two tasks read the source `v1` at the same time
- **THEN** each one reads through its own connection

#### Scenario: Forbidden operation in a task
- **WHEN** a task emits `SuspendConstraints`
- **THEN** the task fails with an error saying that the operation belongs to the preparation phase

#### Scenario: Engine created outside the task
- **WHEN** a script keeps an engine created in `__init__` and a task reads through it in a worker
- **THEN** the read fails with an error saying that the engine belongs to another worker

### Requirement: The main process is the only writer
Workers SHALL send log lines, events, progress, warnings and unit metrics to the main process, and only the main process SHALL write to `migration.log`, `migration.jsonl` and the terminal. Warnings logged once per key SHALL be deduplicated in the main process.

#### Scenario: Log of a task
- **WHEN** a task in a worker process calls `print_log('Inserindo alunos')`
- **THEN** the line is written once to `migration.log` by the main process

#### Scenario: Metrics of the tasks
- **WHEN** a parallel generation finishes
- **THEN** the generation report has the same units, rows read and inserts as the sequential run, in merge order

### Requirement: Compatible entities run as automatic tasks
With more than one worker, each `CopyEntity` SHALL run as a task named `copy:<entity>`. For each source, the row by row copies SHALL be merged before the native copy, as in the sequential run, and the native copy SHALL run as a task whose file is its segment.

#### Scenario: Two entities copied in parallel
- **WHEN** a migration lists two `CopyEntity` from the same source and runs with 2 workers
- **THEN** both are copied by tasks `copy:<entity>` and merged in the listed order

### Requirement: A failing task stops the generation
The first failing task SHALL cancel the tasks not started, SHALL stop the running tasks at their next insert and SHALL fail the generation with the task name and the message. An interruption of the main process SHALL do the same, close the worker connections and record the run as `failed`.

#### Scenario: Task fails
- **WHEN** the task `students` raises while other tasks run
- **THEN** the generation fails with a message naming `students`
- **AND** the manifest status is `failed`

### Requirement: Direct output runs the merged segments in sequence
With the direct or hybrid output, the commands SHALL run on the target in the main process, in merge order, through `include_file()` of each segment. With the `stop` policy, a failing command SHALL cancel the tasks still running.

#### Scenario: Hybrid output with workers
- **WHEN** a migration runs with `--output hybrid --dry-run --workers 4`
- **THEN** the target runs the same commands, in the same order, as with 1 worker

