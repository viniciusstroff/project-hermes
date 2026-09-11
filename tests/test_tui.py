"""
Teste visual do footer TUI — sem conexão com banco.
Executa: python3 tests/test_tui.py
"""
import datetime
import shutil
import sys
import time

FOOTER_HEIGHT = 3


def footer_lines(count, total, table, started_at):
    cols = shutil.get_terminal_size().columns
    inner = cols - 2
    now = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    elapsed = max(time.monotonic() - started_at, 0.001)
    rate = count / elapsed if count > 0 else 0

    ratio = min(count / total, 1.0) if total else 0
    percent = int(ratio * 100)
    count_str = f'{count}/{total}'
    prefix = f' [{now}] {percent:3d}% |'
    suffix = f'| {count_str}  {rate:,.0f} reg/s  {table} '
    bar_width = inner - len(prefix) - len(suffix)
    if bar_width >= 4:
        filled = int(bar_width * ratio)
        content = prefix + '█' * filled + '░' * (bar_width - filled) + suffix
    else:
        content = f' [{now}] {percent:3d}%  {count_str}  {rate:,.0f} reg/s  {table} '

    content = content[:inner].ljust(inner)
    return (
        '┌' + '─' * inner + '┐',
        '│' + content + '│',
        '└' + '─' * inner + '┘',
    )


def render_footer(count, total, table, started_at):
    top, middle, bottom = footer_lines(count, total, table, started_at)
    sys.stdout.write(f'\033[{FOOTER_HEIGHT - 1}A\033[J')
    sys.stdout.write(top + '\r\n' + middle + '\r\n' + bottom)
    sys.stdout.flush()


def print_log(text, count, total, table, started_at):
    now = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    top, middle, bottom = footer_lines(count, total, table, started_at)
    sys.stdout.write(f'\033[{FOOTER_HEIGHT - 1}A\033[J')
    sys.stdout.write(f'[{now}] {text}\r\n')
    sys.stdout.write(top + '\r\n' + middle + '\r\n' + bottom)
    sys.stdout.flush()


def main():
    if not sys.stdout.isatty():
        print('Este teste requer um terminal interativo (TTY).')
        sys.exit(1)

    # Cria espaço para o footer
    sys.stdout.write('\n' * FOOTER_HEIGHT)
    sys.stdout.flush()

    tables = [
        ('acme_customers_demo', 200),
        ('acme_people_demo', 80),
        ('acme_orders_demo', 350),
        ('acme_messages_demo', 120),
    ]

    started_at = time.monotonic()
    total_rows = sum(t for _, t in tables)
    global_count = 0

    for table_name, table_total in tables:
        print_log(f'Inserindo {table_name}', global_count, total_rows, table_name, started_at)

        for i in range(1, table_total + 1):
            time.sleep(0.005)
            global_count += 1
            if i % 50 == 0 or i == table_total:
                print_log(f'  → lote {i}/{table_total} de {table_name}', global_count, total_rows, table_name, started_at)

    # Teardown: sobe cursor até o footer, apaga, finaliza
    sys.stdout.write(f'\033[{FOOTER_HEIGHT - 1}A\033[J')
    sys.stdout.flush()

    elapsed = time.monotonic() - started_at
    print(f'[OK] Concluído: {global_count} registros em {elapsed:.1f}s')


if __name__ == '__main__':
    main()
