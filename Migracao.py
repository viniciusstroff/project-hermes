"""
Ponto de entrada unificado para migrações V1→V2.

Uso:
    python3 Migracao.py <Acme>
    python3 Migracao.py Acme
    python3 Migracao.py Acme --debug 100
"""

import importlib.util
import os
import sys
import traceback

_rootdir = os.path.dirname(os.path.realpath(__file__))

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Uso: python3 Migracao.py <Acme> [--debug <quantidade>]')
        print('Exemplo: python3 Migracao.py Acme')
        print('Exemplo: python3 Migracao.py Acme --debug 100')
        sys.exit(1)

    client = sys.argv[1]
    debug_qty = None

    if '--debug' in sys.argv:
        idx = sys.argv.index('--debug')
        if idx + 1 < len(sys.argv):
            debug_qty = int(sys.argv[idx + 1])
        else:
            debug_qty = 100

    # Adiciona a raiz do projeto ao path para que core/ e common/ sejam importáveis
    sys.path.insert(0, _rootdir)

    client_file = os.path.join(_rootdir, client, f'{client}.py')
    if not os.path.isfile(client_file):
        print(f'[ERRO] Arquivo de migração não encontrado: {client_file}')
        sys.exit(1)

    try:
        spec = importlib.util.spec_from_file_location(client, client_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        m = mod.Migracao()

        if debug_qty is not None:
            m.enable_debug(debug_qty)
            m.run()
        else:
            m.dump_tables()
            m.run()

    except Exception as e:
        print('[ERRO] ' + str(e))
        print(traceback.format_exc())
        sys.exit(1)
