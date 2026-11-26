## ADDED Requirements

### Requirement: Generation report records resumed tasks
The generation report of a resumed run SHALL mark the tasks taken from the checkpoint, with the metrics recorded when they finished, and SHALL list each attempt with its start and end.

#### Scenario: Report after a resume
- **WHEN** a generation resumes and finishes
- **THEN** the report marks `courses`, `students` and `teachers` as resumed
- **AND** lists two attempts
