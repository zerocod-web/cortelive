import sys

from .cli import main
from . import multi

if __name__ == "__main__":
    if multi.e_multi(sys.argv[1:]):     # várias URLs, --lista ou --arquivo repetido
        raise SystemExit(multi.main(sys.argv[1:]))
    raise SystemExit(main())
