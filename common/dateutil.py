import datetime

def validate(date_string, date_format = '%d/$m/%Y'):

    try:
        isValid = bool(datetime.datetime.strptime(date_string, date_format))

    except ValueError:
        isValid = False

    return isValid


