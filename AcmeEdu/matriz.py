"""Roda a AcmeEdu em cada caminho da matriz de prova e resume o resultado.

Uso, dentro de um container na rede hermes-net com os três drivers, com as
bases do profile matriz no ar e preenchidas pelo preparar_matriz.py:

    python3 AcmeEdu/matriz.py [caminho ...]

Sem argumentos, roda todos. Para cada caminho o script troca o AcmeEdu/.env
pelas conexões do caminho, roda a migração com --dry-run, depois com --apply,
e por fim o conferir.py. O .env original volta no fim, mesmo se algo falhar.
O mesmo AcmeEdu.py roda em todos: só as conexões mudam.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ENV_PATH = os.path.join(ROOT, 'AcmeEdu', '.env')
PASSWORD = 'acmeedu'


def postgres(prefix, host, database):
    return [f'{prefix}_ADAPTER=postgres', f'{prefix}_HOST={host}', f'{prefix}_PORT=5432',
            f'{prefix}_NAME={database}', f'{prefix}_USER=acmeedu', f'{prefix}_PASS={PASSWORD}']


def mongodb(prefix, database):
    return [f'{prefix}_ADAPTER=mongodb', f'{prefix}_URI=mongodb://acmeedu-mongo:27017/?replicaSet=rs0',
            f'{prefix}_NAME={database}']


def firebird(prefix, host):
    return [f'{prefix}_ADAPTER=firebird', f'{prefix}_HOST={host}', f'{prefix}_PORT=3050',
            f'{prefix}_NAME=/var/lib/firebird/data/acmeedu_v1.fdb', f'{prefix}_USER=acmeedu',
            f'{prefix}_PASS={PASSWORD}', f'{prefix}_CHARSET=UTF8']


SCENARIOS = {
    'pg96-pg16': postgres('SOURCE_V1', 'acmeedu-v1', 'acmeedu_v1') + postgres('TARGET', 'acmeedu-v2', 'acmeedu_v2'),
    'pg17-pg17': postgres('SOURCE_V1', 'acmeedu-v1-pg17', 'acmeedu_v1') + postgres('TARGET', 'acmeedu-v2-pg17', 'acmeedu_v2'),
    'pg-mongo': postgres('SOURCE_V1', 'acmeedu-v1', 'acmeedu_v1') + mongodb('TARGET', 'acmeedu_v2'),
    'mongo-pg': mongodb('SOURCE_V1', 'acmeedu_v1') + postgres('TARGET', 'acmeedu-v2', 'acmeedu_v2'),
    'fb3-pg': firebird('SOURCE_V1', 'acmeedu-fb3') + postgres('TARGET', 'acmeedu-v2', 'acmeedu_v2'),
    'fb5-pg': firebird('SOURCE_V1', 'acmeedu-fb5') + postgres('TARGET', 'acmeedu-v2', 'acmeedu_v2'),
}

STEPS = (
    ('dry run', [sys.executable, 'Migracao.py', 'AcmeEdu', '--dry-run'], 'Resultado:'),
    ('apply', [sys.executable, 'Migracao.py', 'AcmeEdu', '--apply'], 'Resultado:'),
    ('conferir', [sys.executable, 'AcmeEdu/conferir.py'], 'verificações passaram'),
)


def run_scenario(name):
    with open(ENV_PATH, 'w', encoding='utf-8') as env_file:
        env_file.write('\n'.join(SCENARIOS[name]) + '\n')
    results = []
    for step, command, marker in STEPS:
        process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        output = process.stdout + process.stderr
        summary = next((line.strip() for line in reversed(output.splitlines()) if marker in line), '')
        if process.returncode != 0 and not summary:
            summary = next((line.strip() for line in reversed(output.splitlines()) if '[ERRO]' in line), 'sem saída')
        results.append((step, process.returncode, summary))
        print(f'  {step:<9} código {process.returncode}  {summary}', flush=True)
        if process.returncode != 0:
            break
    return results


def main(names):
    names = names or list(SCENARIOS)
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        print(f'Caminhos desconhecidos: {", ".join(unknown)}. Use: {", ".join(SCENARIOS)}')
        return 2

    backup = ENV_PATH + '.matriz-backup'
    had_env = os.path.exists(ENV_PATH)
    if had_env:
        shutil.copyfile(ENV_PATH, backup)
    failed = []
    try:
        for name in names:
            print(f'{name}:', flush=True)
            results = run_scenario(name)
            if len(results) < len(STEPS) or any(code != 0 for _, code, _ in results):
                failed.append(name)
    finally:
        if had_env:
            shutil.move(backup, ENV_PATH)
        else:
            os.remove(ENV_PATH)

    print(f'\n{len(names) - len(failed)} de {len(names)} caminhos passaram.' + (f' Falharam: {", ".join(failed)}.' if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
