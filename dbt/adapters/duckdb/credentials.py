import os
import time
from dataclasses import dataclass
from dataclasses import field
from functools import lru_cache
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse

import dbt.exceptions
from dbt.adapters.base import Credentials
from dbt.dataclass_schema import dbtClassMixin


@dataclass
class Attachment(dbtClassMixin):
    # The path to the database to be attached (may be a URL)
    path: str

    # The type of the attached database (defaults to duckdb, but may be supported by an extension)
    type: Optional[str] = None

    # An optional alias for the attached database
    alias: Optional[str] = None

    # Whether the attached database is read-only or read/write
    read_only: bool = False

    def to_sql(self) -> str:
        base = f"ATTACH '{self.path}'"
        if self.alias:
            base += f" AS {self.alias}"
        options = []
        if self.type:
            options.append(f"TYPE {self.type}")
        if self.read_only:
            options.append("READ_ONLY")
        if options:
            joined = ", ".join(options)
            base += f" ({joined})"
        return base


@dataclass
class PluginConfig(dbtClassMixin):
    module: str

    alias: Optional[str] = None

    # A plugin-specific set of configuration options
    config: Optional[Dict[str, Any]] = None


@dataclass
class Remote(dbtClassMixin):
    host: str
    port: int
    user: str
    password: Optional[str] = None


@dataclass
class Retries(dbtClassMixin):
    # The number of times to attempt the initial duckdb.connect call
    # (to wait for another process to free the lock on the DB file)
    connect_attempts: int = 1

    # The number of times to attempt to execute a DuckDB query that throws
    # one of the retryable exceptions
    query_attempts: Optional[int] = None

    # The list of exceptions that we are willing to retry on
    retryable_exceptions: List[str] = field(default_factory=lambda: ["IOException"])


@dataclass
class DuckDBCredentials(Credentials):
    database: str = "main"
    schema: str = "main"
    path: str = ":memory:"

    # Any connection-time configuration information that we need to pass
    # to DuckDB (e.g., if we need to enable using unsigned extensions)
    config_options: Optional[Dict[str, Any]] = None

    # any DuckDB extensions we want to install and load (httpfs, parquet, etc.)
    extensions: Optional[Tuple[str, ...]] = None

    # any additional pragmas we want to configure on our DuckDB connections;
    # a list of the built-in pragmas can be found here:
    # https://duckdb.org/docs/sql/configuration
    # (and extensions may add their own pragmas as well)
    settings: Optional[Dict[str, Any]] = None

    # the root path to use for any external materializations that are specified
    # in this dbt project; defaults to "." (the current working directory)
    external_root: str = "."

    # identify whether to use the default credential provider chain for AWS/GCloud
    # instead of statically defined environment variables
    use_credential_provider: Optional[str] = None

    # A list of additional databases that should be attached to the running
    # DuckDB instance to make them available for use in models; see the
    # schema for the Attachment dataclass above for what fields it can contain
    attach: Optional[List[Attachment]] = None

    # A list of filesystems to attach to the DuckDB database via the fsspec
    # interface; see https://duckdb.org/docs/guides/python/filesystems.html
    #
    # Each dictionary entry must have a "fs" entry to indicate which
    # fsspec implementation should be loaded, and then an arbitrary additional
    # number of key-value pairs that will be passed as arguments to the fsspec
    # registry method.
    filesystems: Optional[List[Dict[str, Any]]] = None

    # Used to configure remote environments/connections
    remote: Optional[Remote] = None

    # A list of dbt-duckdb plugins that can be used to customize the
    # behavior of loading source data and/or storing the relations that are
    # created by SQL or Python models; see the plugins module for more details.
    plugins: Optional[List[PluginConfig]] = None

    # Whether to disable transactions when executing SQL statements; this
    # is useful when we would like the resulting DuckDB database file to
    # be as small as possible.
    disable_transactions: bool = False

    # Whether to keep the DuckDB connection open between invocations of dbt
    # (we do this automatically for in-memory or MD connections, but not for
    # local DuckDB files, but this is a way to override that behavior)
    keep_open: bool = False

    # A list of paths to Python modules that should be loaded into the
    # running Python environment when dbt is invoked; this is useful for
    # loading custom dbt-duckdb plugins or locally defined modules that
    # provide helper functions for dbt Python models.
    module_paths: Optional[List[str]] = None

    # An optional strategy for allowing retries when certain types of
    # exceptions occur on a model run (e.g., IOExceptions that were caused
    # by networking issues)
    retries: Optional[Retries] = None

    @property
    def is_motherduck(self):
        parsed = urlparse(self.path)
        return self._is_motherduck(parsed.scheme)

    @staticmethod
    def _is_motherduck(scheme: str) -> bool:
        return scheme in {"md", "motherduck"}

    @classmethod
    def __pre_deserialize__(cls, data: Dict[Any, Any]) -> Dict[Any, Any]:
        data = super().__pre_deserialize__(data)
        path = data.get("path")
        path_db = None
        if path is None or path == ":memory:":
            path_db = "memory"
        else:
            parsed = urlparse(path)
            base_file = os.path.basename(parsed.path)
            path_db = os.path.splitext(base_file)[0]
            # For MotherDuck, turn on disable_transactions unless
            # it's explicitly set already by the user
            if cls._is_motherduck(parsed.scheme):
                if "disable_transactions" not in data:
                    data["disable_transactions"] = True
                if path_db == "":
                    path_db = "my_db"

        if path_db and "database" not in data:
            data["database"] = path_db
        elif path_db and data["database"] != path_db:
            if not data.get("remote"):
                raise dbt.exceptions.DbtRuntimeError(
                    "Inconsistency detected between 'path' and 'database' fields in profile; "
                    f"the 'database' property must be set to '{path_db}' to match the 'path'"
                )
        elif not path_db:
            raise dbt.exceptions.DbtRuntimeError(
                "Unable to determine target database name from 'path' field in profile"
            )
        return data

    @property
    def unique_field(self) -> str:
        """
        This property returns a unique field for the database connection.
        If the connection is remote, it returns the host and port as a string.
        If the connection is local, it returns the path and external root as a string.
        """
        if self.remote:
            return self.remote.host + str(self.remote.port)
        else:
            return self.path + self.external_root

    @property
    def type(self):
        return "duckdb"

    def _connection_keys(self):
        return (
            "database",
            "schema",
            "path",
            "config_options",
            "extensions",
            "settings",
            "external_root",
            "use_credential_provider",
            "attach",
            "filesystems",
            "remote",
            "plugins",
            "disable_transactions",
        )

    def load_settings(self) -> Dict[str, str]:
        settings = self.settings or {}
        if self.use_credential_provider:
            if self.use_credential_provider == "aws":
                settings.update(_load_aws_credentials(ttl=_get_ttl_hash()))
            else:
                raise ValueError(
                    "Unsupported value for use_credential_provider: "
                    + self.use_credential_provider
                )
        return settings


def _get_ttl_hash(seconds=300):
    return round(time.time() / seconds)


@lru_cache()
def _load_aws_credentials(ttl=None) -> Dict[str, Any]:
    """
    Load AWS credentials from the environment.

    This function is cached to prevent unnecessary calls to the AWS API.

    :param ttl: Time to live for the cache. If None, the cache will not expire.
    :return: A dictionary containing the AWS credentials which can be used to configure DuckDB settings.
    """
    del ttl  # make mypy happy
    import boto3.session

    session = boto3.session.Session()

    # use STS to verify that the credentials are valid; we will
    # raise a helpful error here if they are not
    sts = session.client("sts")
    sts.get_caller_identity()

    # now extract/return them
    aws_creds = session.get_credentials().get_frozen_credentials()

    credentials = {
        "s3_access_key_id": aws_creds.access_key,
        "s3_secret_access_key": aws_creds.secret_key,
        "s3_session_token": aws_creds.token,
        "s3_region": session.region_name,
    }
    # only return if value is filled
    return {k: v for k, v in credentials.items() if v}
