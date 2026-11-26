## ADDED Requirements

### Requirement: Generation report records tasks and parallelism
The generation report SHALL record the number of workers and the worker mode, and, for each task, its name, sequence, worker, start, end and duration. Each phase SHALL show its duration next to the sum of the durations of its tasks. The manifest SHALL record the number of workers and the worker mode.

#### Scenario: Parallel load phase
- **WHEN** the load phase runs 6 tasks with 4 workers
- **THEN** the report lists the 6 tasks with their worker and duration
- **AND** the phase shows its duration and the sum of the task durations
