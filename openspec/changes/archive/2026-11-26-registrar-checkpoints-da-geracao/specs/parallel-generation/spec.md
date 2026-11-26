## ADDED Requirements

### Requirement: Finished segments are reused on resume
A task that finished in a worker but was not merged before the failure SHALL have its complete segment and sha256 recorded in the checkpoint, and on resume it SHALL be merged without running again when the segment matches the recorded sha256.

#### Scenario: Task finished out of order
- **WHEN** `teachers` finished before `students` failed, both in sequence 1, and the generation resumes
- **THEN** `teachers` is merged from its segment without running again
- **AND** `students` runs again
