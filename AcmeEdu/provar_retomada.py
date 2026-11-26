"""Prova de ponta a ponta do checkpoint da geração com a AcmeEdu.

Uso, dentro de um container na rede hermes-net, com a V1 e a V2 da AcmeEdu no
ar e o AcmeEdu/.env apontando para elas:

    python3 AcmeEdu/provar_retomada.py [--aplicar] [--manter]

O script:

1. gera a AcmeEdu do zero, como referência;
2. para cada modo (em sequência e com 4 threads), começa outra geração e
   mata o processo com SIGKILL assim que o checkpoint.json confirma a
   primeira tarefa da carga, como numa queda do servidor de geração;
3. retoma com --resume e confere que a retomada termina, que as tarefas
   confirmadas não rodaram de novo e que os arquivos de comandos são iguais,
   byte a byte, aos da referência;
4. confere as recusas: retomar uma execução que já terminou e retomar com
   outro limite de debug, sem mudar nada na pasta.

Com --aplicar, a última retomada também aplica no destino (--apply) e o
conferir.py roda no fim. Com --manter, as pastas criadas em runs/AcmeEdu
ficam; sem ele, saem quando todas as verificações passam.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
CLIENT = 'AcmeEdu'
LOAD_PHASE = 'load_transformed_data'
MODES = (('em sequência', []), ('4 threads', ['--workers', '4']))
KILL_TIMEOUT = 120


class Check:
    def __init__(self, name, ok, detail=''):
        self.name = name
        self.ok = bool(ok)
        self.detail = detail


def migrate(*args):
    """Roda o Migracao.py e devolve o código de saída, a saída e a pasta da execução."""

    process = subprocess.run(
        [sys.executable, 'Migracao.py', CLIENT, *args], cwd=ROOT, capture_output=True, text=True,
    )
    output = process.stdout + process.stderr
    return process.returncode, output, run_dir_in(output)


def run_dir_in(output):
    match = re.search(r'Artefatos da execução: (\S+)', output)
    return match.group(1) if match else None


def generate_and_kill(*args):
    """Começa uma geração e a mata assim que a primeira tarefa da carga é confirmada.

    Devolve a pasta da execução e quantas tarefas da carga estavam confirmadas.
    O processo roda num grupo próprio, para o SIGKILL levar junto os workers.
    """

    with tempfile.NamedTemporaryFile('w+', suffix='.log', delete=False) as log:
        log_path = log.name
    with open(log_path, 'w', encoding='utf-8') as log:
        process = subprocess.Popen(
            [sys.executable, 'Migracao.py', CLIENT, *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + KILL_TIMEOUT
    run_dir = None
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if run_dir is None:
                with open(log_path, encoding='utf-8') as log:
                    run_dir = run_dir_in(log.read())
            elif len(confirmed(run_dir)) >= 1:
                os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(0.005)
        process.wait()
    finally:
        os.remove(log_path)
    return run_dir, confirmed(run_dir) if run_dir else []


def confirmed(run_dir):
    """Tarefas da carga confirmadas no checkpoint.json, que é trocado de forma atômica."""

    path = os.path.join(run_dir, 'checkpoint.json')
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as checkpoint_file:
        phase = json.load(checkpoint_file)['phases'].get(LOAD_PHASE) or {}
    return [mark['name'] for mark in phase.get('marks', ())]


def read_json(run_dir, name):
    with open(os.path.join(run_dir, name), encoding='utf-8') as json_file:
        return json.load(json_file)


def commands(run_dir):
    folder = os.path.join(run_dir, 'comandos')
    result = {}
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), 'rb') as artifact:
            result[name] = artifact.read()
    return result


def timestamp(run_dir):
    return os.path.basename(run_dir)


def prove_mode(label, args, reference, apply):
    checks = []
    run_dir, done = generate_and_kill(*args)
    if run_dir is None:
        return [Check(f'{label}: geração começou', False, 'a pasta da execução não apareceu na saída')], None
    status = read_json(run_dir, 'manifest.json')['status']
    checks.append(Check(
        f'{label}: geração interrompida no meio da carga', status != 'success' and done,
        f'status {status}, confirmadas {done}' if status != 'success' else 'terminou antes do SIGKILL; rode de novo',
    ))
    if status == 'success':
        return checks, run_dir

    code, output, _ = migrate('--resume', timestamp(run_dir), *args, *(['--apply'] if apply else []))
    manifest = read_json(run_dir, 'manifest.json')
    checks.append(Check(f'{label}: retomada termina com sucesso', code == 0 and manifest['status'] == 'success',
                        manifest.get('error') or output.strip().splitlines()[-1:]))

    report = read_json(run_dir, 'relatorio-geracao.json')
    resumed = {task['name'] for task in report['tasks'] if task.get('resumed')}
    checks.append(Check(f'{label}: tarefas confirmadas não rodaram de novo', set(done) <= resumed,
                        f'confirmadas {done}, retomadas {sorted(resumed)}'))

    different = [name for name, data in commands(run_dir).items() if reference.get(name) != data]
    checks.append(Check(f'{label}: arquivos iguais aos da referência', not different and len(reference) == 5,
                        f'diferentes: {different}' if different else '5 arquivos'))

    attempts = [attempt['status'] for attempt in report.get('attempts') or ()]
    checks.append(Check(f'{label}: duas tentativas registradas', len(attempts) == 2 and attempts[-1] == 'success',
                        f'tentativas {attempts}'))
    checks.append(Check(f'{label}: segmentos removidos', not os.path.exists(os.path.join(run_dir, 'tarefas'))))
    return checks, run_dir


def prove_refusals(finished_dir):
    checks = []
    code, output, _ = migrate('--resume', timestamp(finished_dir))
    checks.append(Check('recusa retomar uma execução que já terminou', code == 1 and 'já terminou' in output,
                        f'código {code}'))

    run_dir, done = generate_and_kill()
    if run_dir is None or not done:
        checks.append(Check('recusa retomar com outro limite de debug', False, 'a geração não foi interrompida'))
        return checks, [run_dir] if run_dir else []
    before = commands(run_dir)
    code, output, _ = migrate('--resume', timestamp(run_dir), '--debug', '10')
    manifest = read_json(run_dir, 'manifest.json')
    checks.append(Check(
        'recusa retomar com outro limite de debug, sem mudar a pasta',
        manifest['status'] == 'failed' and 'limite de debug' in (manifest.get('error') or '') and commands(run_dir) == before,
        manifest.get('error'),
    ))
    # A recusa não pode estragar o checkpoint: a retomada seguinte ainda pula as tarefas confirmadas.
    code, _, _ = migrate('--resume', timestamp(run_dir))
    report = read_json(run_dir, 'relatorio-geracao.json')
    resumed = {task['name'] for task in report['tasks'] if task.get('resumed')}
    checks.append(Check(
        'depois da recusa, a retomada ainda pula as tarefas confirmadas',
        code == 0 and read_json(run_dir, 'manifest.json')['status'] == 'success' and set(done) <= resumed,
        f'código {code}, confirmadas {done}, retomadas {sorted(resumed)}',
    ))
    return checks, [run_dir]


def conferir():
    process = subprocess.run([sys.executable, os.path.join('AcmeEdu', 'conferir.py')], cwd=ROOT,
                             capture_output=True, text=True)
    last = (process.stdout.strip().splitlines() or [''])[-1]
    return Check('conferir.py depois da retomada com --apply', process.returncode == 0, last)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prova de ponta a ponta do checkpoint da geração com a AcmeEdu.')
    parser.add_argument('--aplicar', action='store_true', help='a última retomada aplica no destino e o conferir.py roda')
    parser.add_argument('--manter', action='store_true', help='mantém as pastas criadas em runs/AcmeEdu')
    args = parser.parse_args(argv)

    print('Gerando a referência', flush=True)
    code, output, reference_dir = migrate()
    if code != 0 or reference_dir is None:
        print('[ERRO] A geração de referência falhou:\n' + output)
        return 1
    reference = commands(reference_dir)
    created = [reference_dir]

    checks = []
    last_dir = None
    for index, (label, mode_args) in enumerate(MODES):
        print(f'Interrompendo e retomando: {label}', flush=True)
        apply = args.aplicar and index == len(MODES) - 1
        mode_checks, run_dir = prove_mode(label, mode_args, reference, apply)
        checks += mode_checks
        if run_dir:
            created.append(run_dir)
            last_dir = run_dir
    if args.aplicar and last_dir:
        checks.append(conferir())

    print('Conferindo as recusas', flush=True)
    refusal_checks, refusal_dirs = prove_refusals(last_dir or reference_dir)
    checks += refusal_checks
    created += refusal_dirs

    width = max(len(check.name) for check in checks)
    print()
    for check in checks:
        status = 'ok    ' if check.ok else 'FALHOU'
        detail = f'  {check.detail}' if check.detail and not check.ok else ''
        print(f'{status} {check.name:<{width}}{detail}')
    failed = [check for check in checks if not check.ok]
    print(f'\n{len(checks) - len(failed)} de {len(checks)} verificações passaram.')

    if not failed and not args.manter:
        for run_dir in dict.fromkeys(created):
            shutil.rmtree(run_dir, ignore_errors=True)
    elif failed:
        print('Pastas mantidas para conferência: ' + ', '.join(timestamp(path) for path in dict.fromkeys(created)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
