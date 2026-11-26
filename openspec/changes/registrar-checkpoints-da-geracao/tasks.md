## 1. Prerequisites

- [ ] 1.1 Implement and archive `paralelizar-geracao-de-tarefas-independentes`.

## 2. Checkpoint

- [ ] 2.1 Create `core/checkpoint.py` with the checkpoint record, atomic writes (temporary file and `os.replace`) and a `flock` on the run directory.
- [ ] 2.2 Compute the run fingerprint after target reads close: core version, adapters and server versions, connections and URI hash, debug limit, output, reference map hash and target catalog.
- [ ] 2.3 Make `FileOutput` compute the sha256 of each task while writing and flush and sync the file after each merged task, recording positions in the checkpoint.
- [ ] 2.4 Record the source row count of each unit and a hash of the method code of each task.

## 3. Resume

- [ ] 3.1 Add `--resume <timestamp>` to `Migracao.py`, reopening the run directory through `ArtifactManager(timestamp=...)` and refusing status `success` and the direct and hybrid outputs.
- [ ] 3.2 Check the fingerprint and the sha256 of the recorded ranges, then truncate each phase artifact after the last confirmed task.
- [ ] 3.3 Run the hooks in skip mode up to the cut: render and check by size the operations outside tasks, skip the tasks before the cut, keep recording run state such as suspended constraints, and refuse on divergence.
- [ ] 3.4 Merge finished but unmerged parallel segments that match their sha256 without running them again.
- [ ] 3.5 Count the units of finished tasks again on the source and refuse the resume on a different count.
- [ ] 3.6 Warn about finished tasks whose code changed and add `--rerun <name>`.

## 4. End of the Run

- [ ] 4.1 Remove the remaining segments on success and keep `checkpoint.json` with status `success`.
- [ ] 4.2 Make the runner handle `BaseException`, recording `failed` and the checkpoint before raising an interruption.
- [ ] 4.3 Mark resumed tasks and list the attempts in the generation report.

## 5. Documentation

- [ ] 5.1 Document `--resume`, `--rerun`, the fingerprint, the source check and the limits in `docs/execucao.md`.
- [ ] 5.2 Document the checkpoint and the skip mode in `docs/arquitetura.md`, and the rule that hooks only emit operations in `docs/novo-cliente.md`.
- [ ] 5.3 Mark item 13 of `docs/melhorias-futuras.md` with what was implemented and what remains.

## 6. Verification

- [ ] 6.1 Add tests for the checkpoint record, atomic writes and the lock.
- [ ] 6.2 Add tests with fake sources: a generation that fails in the third task and resumes ends with the same artifact bytes as a clean generation, with 1 worker and with 4 workers.
- [ ] 6.3 Add tests for refusals: status `success`, changed fingerprint, divergence before the cut, changed source count, direct output.
- [ ] 6.4 Add tests for `--rerun`, changed task code warnings and unmerged segments reused on resume.
- [ ] 6.5 Run AcmeEdu, stop the source container during the load phase, start it again and resume; compare the artifacts with a clean run and run `conferir.py` after applying.
- [ ] 6.6 Run `python3 -m unittest discover -s tests` and `openspec validate registrar-checkpoints-da-geracao --strict`.
