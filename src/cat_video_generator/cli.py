from __future__ import annotations

import typer

from .commands.content import (
    approve_pack,
    canon_import,
    import_pack,
    review,
    validate_pack,
)
from .commands.database import (
    db_current,
    db_upgrade,
    db_validate_remote,
    db_validate_runtime,
    doctor,
)
from .commands.delivery import deliver
from .commands.generation import (
    reconcile_job,
    resume,
    retry_slot,
    run_next,
    run_pack,
    status,
)
from .config import load_local_env

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
db_app = typer.Typer(no_args_is_help=True)
canon_app = typer.Typer(no_args_is_help=True)

app.command()(doctor)
app.command("validate-pack")(validate_pack)
app.command("import-pack")(import_pack)
app.command("approve-pack")(approve_pack)
app.command()(review)
app.command()(status)
app.command("run-next")(run_next)
app.command("run-pack")(run_pack)
app.command("retry-slot")(retry_slot)
app.command()(resume)
app.command("reconcile-job")(reconcile_job)
app.command()(deliver)

db_app.command("upgrade")(db_upgrade)
db_app.command("current")(db_current)
db_app.command("validate-remote")(db_validate_remote)
db_app.command("validate-runtime")(db_validate_runtime)
canon_app.command("import")(canon_import)

app.add_typer(db_app, name="db")
app.add_typer(canon_app, name="canon")


def main() -> None:
    load_local_env()
    app()


if __name__ == "__main__":
    main()
