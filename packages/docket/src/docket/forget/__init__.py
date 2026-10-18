"""Forgetting a record for real, on every clone (#182): the apply, the plan with its dry run and screen, the history
rewrite's files, and `forget()`, which only the `docket forget` command calls.

| Module | What |
|---|---|
| `apply` | the forgotten list against this clone's tree, staging and key store; every gate call runs it first |
| `plan` | the family a forget takes, the dry run's counts and the `--now` screen's ids |
| `rewrite` | the `git filter-repo` files a forget writes, and `--check-rewrite`, which runs them on each branch tip |
| `now` | `forget()`: the console, the question, the plan again under the lock, the order of writes |

The list itself and the docs' rule are the vault's: `vault/forgotten.py` and `vault/forgetdocs.py`.
"""
