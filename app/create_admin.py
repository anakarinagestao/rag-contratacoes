"""Interactive local bootstrap: python -m app.create_admin."""

from getpass import GetPassWarning, getpass
import sys
import warnings

from pydantic import ValidationError

from app.core.database import SessionLocal, init_db
from app.modules.admin.services import AdminAlreadyExistsError, create_first_admin
from app.modules.auth.schemas import UserCreate
from app.modules.auth.services import UserConflictError


def main() -> int:
    if not sys.stdin.isatty():
        print("Execute em um terminal interativo para informar a senha com segurança.", file=sys.stderr)
        return 1
    try:
        username = input("Username: ")
        email = input("Email: ")
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("Senha (12 a 128 caracteres): ")
            confirmation = getpass("Confirme a senha: ")
        if password != confirmation:
            print("As senhas não coincidem.", file=sys.stderr)
            return 1
        data = UserCreate(username=username, email=email, password=password)
        init_db()
        with SessionLocal() as db:
            user = create_first_admin(db, data)
            print(f"Administrador criado: {user.username} (id={user.id}).")
        return 0
    except ValidationError as exc:
        # Never print validation input: it may contain a plaintext password.
        for error in exc.errors(include_input=False, include_url=False):
            print(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}", file=sys.stderr)
        return 1
    except (AdminAlreadyExistsError, UserConflictError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt, GetPassWarning):
        print("Criação cancelada; é necessário um terminal com senha oculta.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
