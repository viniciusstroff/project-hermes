# generation-checkpoints Specification

## Purpose
Define how a generation with the file output records a checkpoint after each task, and how a failed generation resumes in the same run directory, skipping the finished tasks and refusing the resume when the run, the source or the script changed.
## Requirements
### Requirement: Finished tasks are recorded in a checkpoint
After each task is merged into its phase artifact, the generation SHALL flush and sync the artifact and SHALL record in `checkpoint.json`, at the run root, the task name, phase and sequence, the start and end positions of its commands in the artifact, the sha256 of those bytes, the source row count of each of its units and a hash of the method code. The checkpoint SHALL be written atomically, and a lock on the run directory SHALL keep two processes from writing it at the same time.

#### Scenario: Task merged
- **WHEN** the task `students` is merged into `03_load_transformed_data.sql`
- **THEN** `checkpoint.json` records `students` with the positions and the sha256 of its commands in that file

#### Scenario: Second process on the same run
- **WHEN** a second `Migracao.py --resume` starts on a run directory locked by a running generation
- **THEN** it exits with an error saying that the run is in use

### Requirement: A failed generation resumes in the same run directory
`Migracao.py --resume <timestamp>` SHALL reopen the run directory, SHALL accept the status `running` or `failed` and refuse `success`, SHALL truncate each phase artifact after the last task confirmed in merge order and SHALL run the hooks again, skipping what comes before the cut: operations outside tasks are rendered and checked by size without being written, and tasks before the cut do not run. Effects of skipped operations on the run state, such as suspended constraints, SHALL still be recorded. After the cut, the generation SHALL go on normally.

#### Scenario: Resume after a source failure
- **WHEN** a generation fails in the task `enrollments` after `courses`, `students` and `teachers` were merged, and then runs with `--resume`
- **THEN** `courses`, `students` and `teachers` do not run again
- **AND** the phase artifacts end with the same bytes as a clean generation

#### Scenario: Run already finished
- **WHEN** `--resume` names a run whose status is `success`
- **THEN** it exits with an error saying that the run is finished

#### Scenario: Divergence before the cut
- **WHEN** the skipped operations before the cut render to a size different from the recorded positions
- **THEN** the resume is refused with a message naming the phase and the position

### Requirement: Resume refuses an incompatible run
The checkpoint SHALL record a fingerprint of the run: a hash of the source files of `core/`, adapters and server versions, connection descriptions without passwords plus a hash of the URI, debug limit, output type and format, a hash of the loaded reference maps and the target catalog used to render `SuspendConstraints`. A resume with a different fingerprint SHALL be refused, naming what changed. The number of workers SHALL NOT be part of the fingerprint.

#### Scenario: Reference map changed
- **WHEN** a row of the reference map `student_statuses` changed in the target between the failure and the resume
- **THEN** the resume is refused, naming the reference maps

#### Scenario: Different number of workers
- **WHEN** a generation fails with 4 workers and resumes with 2
- **THEN** the resume goes on

### Requirement: Changed task code is reported and can be rerun
A finished task whose method code changed SHALL produce a warning with its name and SHALL NOT run again by itself. `--rerun <name>` SHALL cut the phase at the start of that task, so the task and every task after it in the phase run again.

#### Scenario: Fixed transformation
- **WHEN** the code of the finished task `students` changed and the generation resumes with `--rerun students`
- **THEN** `students` and the tasks after it in the load phase run again

### Requirement: Source changes refuse the resume
On resume, the units of the finished tasks SHALL be counted again on the source, and a count different from the recorded one SHALL refuse the resume.

#### Scenario: Rows added to the source
- **WHEN** rows were added to `tb_aluno` after the task `students` finished
- **THEN** the resume is refused, naming `students` and both counts

### Requirement: Resume works with the file output only
`--resume` SHALL be refused with the direct and hybrid outputs.

#### Scenario: Resume with direct output
- **WHEN** `Migracao.py --resume <timestamp> --output direct --apply` runs
- **THEN** it exits with an argument error before connecting

### Requirement: Each new run gets its own directory
A new generation SHALL create a run directory of its own, adding a suffix to the timestamp when another run already used it, so two runs started in the same second never share artifacts or checkpoint. A resume SHALL reopen the directory it names.

#### Scenario: Two runs in the same second
- **WHEN** two generations of the same client start in the same second
- **THEN** they write to `runs/<cliente>/<timestamp>/` and `runs/<cliente>/<timestamp>-2/`

### Requirement: The run state survives the end of the generation
When the generation succeeds, the remaining segments SHALL be removed and `checkpoint.json` SHALL keep the status `success`. When the generation is interrupted, the runner SHALL record `failed` in the manifest and save the checkpoint before raising the interruption.

#### Scenario: Interrupted generation
- **WHEN** the operator presses Ctrl+C during the load phase
- **THEN** the manifest status is `failed` and `checkpoint.json` lists the tasks merged so far

