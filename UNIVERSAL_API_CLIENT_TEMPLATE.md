# Universal API Client Template

A template for building multi-service CLI tools that wrap multiple APIs with shared configuration, environment management, and auto-registration.

## Architecture Overview

```
my_api_client/
├── __init__.py                     # Package exports for script usage
├── cli.py                          # Main CLI entry point
├── core/                           # Shared infrastructure
│   ├── __init__.py
│   ├── service_registry.py         # Auto-registration system
│   ├── base_service.py             # Base classes (service + config)
│   ├── base_client.py              # Base HTTP client
│   ├── decorator_utils.py          # @service_command decorators
│   └── exceptions.py               # Standard exceptions
├── utils/                          # Shared utilities
│   ├── env_utils.py                # Environment enum
│   └── config_utils.py             # Configuration management
└── [service_name]/                 # One directory per service
    ├── __init__.py
    ├── service.py                  # Service definition (auto-registers)
    ├── client.py                   # HTTP API client
    ├── cli.py                      # CLI commands
    └── models.py                   # Pydantic request/response models
```

---

## Core Files

### 1. `utils/env_utils.py` - Environment Enum

```python
from enum import Enum


class Environment(str, Enum):
    dev = "dev"
    staging = "staging"
    prod = "prod"
    local = "local"
```

---

### 2. `core/service_registry.py` - Auto-Registration

```python
from typing import Dict, Type, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from my_api_client.core.base_service import BaseServiceCLI


class ServiceRegistry:
    _services: Dict[str, Type["BaseServiceCLI"]] = {}

    @classmethod
    def register(cls, service_class: Type["BaseServiceCLI"]) -> None:
        service_name = service_class.SERVICE_NAME
        if service_name is None:
            raise ValueError(f"Service class {service_class.__name__} has no SERVICE_NAME")
        if service_name in cls._services:
            raise ValueError(f"Service {service_name} already registered")
        cls._services[service_name] = service_class

    @classmethod
    def get_all(cls) -> Dict[str, Type["BaseServiceCLI"]]:
        return cls._services.copy()

    @classmethod
    def get(cls, service_name: str) -> Optional[Type["BaseServiceCLI"]]:
        return cls._services.get(service_name)

    @classmethod
    def clear(cls) -> None:
        cls._services.clear()
```

---

### 3. `core/base_service.py` - Base Classes

```python
from typing import Type, List, Tuple, Any, Optional, TypeVar, ClassVar, Callable, TYPE_CHECKING
import typer
from pydantic import BaseModel
from rich.console import Console

if TYPE_CHECKING:
    from my_api_client.utils.env_utils import Environment

T = TypeVar("T")


class BaseServiceConfig(BaseModel):
    """Base configuration class for all services."""

    @property
    def is_configured(self) -> bool:
        return any(
            getattr(self, field_name) is not None
            for field_name in self.__class__.model_fields
        )

    def mask_secret(self, value: Optional[str]) -> str:
        if not value:
            return "[red]Not set[/red]"
        if len(value) <= 4:
            return "*" * len(value)
        return "*" * (len(value) - 4) + value[-4:]

    def display_fields(self) -> List[Tuple[str, str, Optional[Callable]]]:
        """Return [(label, field_name, formatter_fn), ...] for config display."""
        return [
            (
                field.description or field_name.replace("_", " ").title(),
                field_name,
                None,
            )
            for field_name, field in self.__class__.model_fields.items()
        ]


class BaseServiceCLI:
    """Base class for all service implementations with auto-registration."""

    SERVICE_NAME: ClassVar[Optional[str]] = None
    DISPLAY_NAME: ClassVar[Optional[str]] = None
    CONFIG_CLASS: ClassVar[Optional[Type[BaseServiceConfig]]] = None

    console: Console = Console()

    def __init_subclass__(cls, **kwargs):
        """Auto-register services when class is defined."""
        super().__init_subclass__(**kwargs)

        required_attrs = [
            ("SERVICE_NAME", str),
            ("DISPLAY_NAME", str),
            ("CONFIG_CLASS", type),
        ]

        for attr_name, expected_type in required_attrs:
            value = getattr(cls, attr_name, None)
            if value is None:
                raise ValueError(f"{cls.__name__} must define {attr_name}")
            if not isinstance(value, expected_type):
                raise TypeError(
                    f"{cls.__name__}.{attr_name} must be {expected_type.__name__}"
                )

        from my_api_client.core.service_registry import ServiceRegistry
        ServiceRegistry.register(cls)

    def get_config_for_env(self, env_name: str) -> BaseServiceConfig:
        from my_api_client.utils.config_utils import load_config

        config = load_config()
        env_config = config.global_settings.environments.get(env_name)
        if not env_config:
            raise ValueError(f"Environment {env_name} not found")

        service_attr = self.__class__.SERVICE_NAME.replace("-", "_")
        return getattr(env_config, service_attr)

    def get_current_config(self, env: Optional["Environment"] = None) -> BaseServiceConfig:
        from my_api_client.utils.config_utils import load_config

        config = load_config()
        effective_env = env or config.global_settings.default_env

        if not effective_env:
            raise ValueError("No environment specified and no default set")

        return self.get_config_for_env(effective_env.value)

    def output_json(self, data: Any, pretty: bool = True) -> None:
        if data is None:
            self.console.print("[yellow]No data returned[/yellow]")
            return

        if hasattr(data, "model_dump"):
            data = data.model_dump(mode="json")
        elif hasattr(data, "dict"):
            data = data.dict()

        if pretty:
            self.console.print_json(data=data)
        else:
            import json
            print(json.dumps(data))

    def _error_exit(self, message: str, suggestion: Optional[str] = None) -> None:
        self.console.print(f"[bold red]Error: {message}[/bold red]")
        if suggestion:
            self.console.print(f"[dim]{suggestion}[/dim]")
        raise typer.Exit(code=1)

    def require_not_none(
        self, value: Optional[T], error_message: str, suggestion: Optional[str] = None
    ) -> T:
        if value is None:
            self._error_exit(error_message, suggestion)
        return value

    def display_config(self, console: Console, env: str, indent: str = "  ") -> None:
        console.print(f"{indent}[bold]{self.DISPLAY_NAME}:[/bold]")
        try:
            service_config = self.get_config_for_env(env)
            if not service_config.is_configured:
                console.print(f"{indent}  [dim]Not configured[/dim]")
            else:
                for label, field, formatter in service_config.display_fields():
                    value = getattr(service_config, field, None)
                    if formatter:
                        value = formatter(value)
                    else:
                        value = value or "[red]Not set[/red]"
                    console.print(f"{indent}  {label}: {value}")
        except Exception as e:
            console.print(f"{indent}  [dim]Error: {e}[/dim]")

    def create_cli_app(self) -> typer.Typer:
        raise NotImplementedError(f"{self.__class__.__name__} must implement create_cli_app()")

    def get_client(self, ctx: Optional[typer.Context], env: Optional["Environment"], **kwargs):
        raise NotImplementedError(f"{self.__class__.__name__} must implement get_client()")
```

---

### 4. `core/base_client.py` - Base HTTP Client

```python
from typing import ClassVar, Dict, Optional, Any, Type
import requests
from my_api_client.utils.env_utils import Environment
from my_api_client.utils.config_utils import load_config
from my_api_client.core.base_service import BaseServiceConfig


class BaseApiClient:
    """Base class for all API clients with common HTTP patterns."""

    ENVIRONMENT_URL_MAP: ClassVar[Dict[Environment, str]] = {}
    DEFAULT_HEADERS: ClassVar[Dict[str, str]] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    def __init__(
        self,
        env: Environment = Environment.prod,
        timeout: int = 30,
        verbose: bool = False,
        **auth_params,
    ):
        self.env = env
        self.timeout = timeout
        self.verbose = verbose
        self.base_url = self._resolve_base_url()
        self.session = self._create_session(**auth_params)

        if self.verbose:
            self.session.hooks["response"] = [self._log_response]

    def _resolve_base_url(self) -> str:
        if self.env == Environment.local:
            for fallback in [Environment.dev, Environment.staging]:
                if fallback in self.ENVIRONMENT_URL_MAP:
                    return self.ENVIRONMENT_URL_MAP[fallback].rstrip("/")

        url = self.ENVIRONMENT_URL_MAP.get(self.env)
        if not url:
            raise ValueError(f"No URL configured for environment: {self.env}")
        return url.rstrip("/")

    def _create_session(self, **auth_params) -> requests.Session:
        session = requests.Session()
        session.headers.update(self.DEFAULT_HEADERS)
        return session

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
    ) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        return self.session.request(
            method=method,
            url=url,
            params=params,
            json=json_data,
            headers=headers,
            timeout=self.timeout,
        )

    def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        response = self._request("GET", path, params=params)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, json_data: Optional[Dict] = None) -> Dict[str, Any]:
        response = self._request("POST", path, json_data=json_data)
        response.raise_for_status()
        return response.json()

    def _log_response(self, response, *args, **kwargs):
        print(f"\n[VERBOSE] {response.request.method} {response.url}")
        print(f"  Status: {response.status_code}")
        try:
            print(f"  Body: {response.json()}")
        except Exception:
            print(f"  Body: {response.text[:500]}")

    @classmethod
    def from_config(cls, env: Optional[Environment] = None, **overrides) -> "BaseApiClient":
        config = load_config()
        effective_env = env or config.global_settings.default_env

        if not effective_env:
            raise Exception("No environment specified and no default set")

        env_config = config.get_env_config(effective_env)
        service_config = getattr(env_config, cls._get_config_field_name())

        return cls._create_from_config(effective_env, service_config, overrides)

    @classmethod
    def _create_from_config(
        cls, env: Environment, config: BaseServiceConfig, overrides: Dict[str, Any]
    ) -> "BaseApiClient":
        raise NotImplementedError(f"{cls.__name__} must implement _create_from_config")

    @classmethod
    def _get_config_field_name(cls) -> str:
        return cls.__name__.lower()
```

---

### 5. `core/decorator_utils.py` - @service_command Decorator

```python
import functools
import inspect
from typing import Any, Tuple, Optional, List, Dict, Type, Callable, TypeVar, TYPE_CHECKING
import typer

if TYPE_CHECKING:
    from my_api_client.core.base_service import BaseServiceCLI

F = TypeVar("F", bound=Callable[..., Any])


def extract_context_from_args(
    args: Tuple[Any, ...], kwargs: Dict[str, Any], pass_context: bool
) -> Tuple[Optional[typer.Context], Tuple[Any, ...]]:
    ctx = None
    if args and isinstance(args[0], typer.Context):
        ctx = args[0]
        if not pass_context:
            args = args[1:]
    elif "ctx" in kwargs:
        ctx = kwargs.pop("ctx")
    return ctx, args


def extract_common_params(kwargs: Dict[str, Any], param_names: List[str]) -> Dict[str, Any]:
    extracted = {}
    for param in param_names:
        if param in kwargs:
            extracted[param] = kwargs.pop(param)
    return extracted


def create_common_cli_params(
    env: bool = True,
    pretty: bool = True,
    verbose: bool = False,
) -> List[inspect.Parameter]:
    from typing import Annotated
    from my_api_client.utils.env_utils import Environment

    params = []

    if env:
        params.append(
            inspect.Parameter(
                "env",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[
                    Optional[Environment],
                    typer.Option("--env", "-e", help="Environment (overrides default)"),
                ],
            )
        )

    if pretty:
        params.append(
            inspect.Parameter(
                "pretty",
                inspect.Parameter.KEYWORD_ONLY,
                default=True,
                annotation=Annotated[
                    bool,
                    typer.Option("--pretty/--no-pretty", help="Pretty print JSON"),
                ],
            )
        )

    if verbose:
        params.append(
            inspect.Parameter(
                "verbose",
                inspect.Parameter.KEYWORD_ONLY,
                default=False,
                annotation=Annotated[
                    bool,
                    typer.Option("--verbose", "-v", help="Show HTTP details"),
                ],
            )
        )

    return params


def modify_signature_for_typer(
    func: Any,
    client_class: Type[Any],
    pass_context: bool,
    params_to_add: Optional[List[inspect.Parameter]] = None,
) -> None:
    try:
        original_sig = inspect.signature(func)
        params = []

        for param_name, param in original_sig.parameters.items():
            if (
                param.annotation == client_class
                or param_name == "client"
                or (hasattr(param.annotation, "__name__") and "Client" in param.annotation.__name__)
            ):
                continue

            if param_name == "ctx" and param.annotation == typer.Context:
                if pass_context:
                    params.append(param)
                continue

            params.append(param)

        if not any(p.name == "ctx" for p in params):
            ctx_param = inspect.Parameter(
                "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=typer.Context
            )
            params.insert(0, ctx_param)

        if params_to_add:
            existing_names = {p.name for p in params}
            for new_param in params_to_add:
                if new_param.name not in existing_names:
                    params.append(new_param)

        func.__signature__ = original_sig.replace(parameters=params)
    except (ValueError, TypeError, AttributeError):
        pass


def service_command(
    service_class: Type["BaseServiceCLI"],
    *,
    pass_context: bool = True,
    env: bool = True,
    pretty: bool = True,
    verbose: bool = False,
) -> Callable[[F], F]:
    """Universal decorator for service commands.

    Automatically:
    1. Extracts --env, --pretty, --verbose from CLI args
    2. Creates service instance and calls get_client()
    3. Calls decorated function with client injected
    4. Formats output using service.output_json()
    5. Handles errors consistently

    Usage:
        @app.command("get-user")
        @service_command(MyServiceClass, pass_context=False)
        def get_user(client: MyClient, user_id: str):
            return client.get_user(user_id)
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx, args = extract_context_from_args(args, kwargs, pass_context)

            param_names = []
            if env:
                param_names.append("env")
            if pretty:
                param_names.append("pretty")
            if verbose:
                param_names.append("verbose")

            common_params = extract_common_params(kwargs, param_names)

            service = service_class()

            try:
                client = service.get_client(
                    ctx=ctx,
                    env=common_params.get("env"),
                    verbose=common_params.get("verbose", False),
                )

                if pass_context and ctx:
                    result = func(ctx, client, *args, **kwargs)
                else:
                    result = func(client, *args, **kwargs)

                if result is not None:
                    service.output_json(result, pretty=common_params.get("pretty", True))

            except typer.Exit:
                raise
            except Exception as e:
                service._error_exit(f"Error in {func.__name__}: {e}")

        common_param_list = create_common_cli_params(
            env=env, pretty=pretty, verbose=verbose
        )
        modify_signature_for_typer(wrapper, Any, pass_context, common_param_list)

        return wrapper

    return decorator
```

---

### 6. `utils/config_utils.py` - Configuration System

```python
import json
import os
from pathlib import Path
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from my_api_client.utils.env_utils import Environment
from my_api_client.core.base_service import BaseServiceConfig

APP_NAME = "my_api_client"
CONFIG_FILE_NAME = "config.json"


# --- Service Configs (add one per service) ---

class ExampleServiceConfig(BaseServiceConfig):
    api_key: Optional[str] = Field(None, description="API Key")
    base_url: Optional[str] = Field(None, description="Base URL")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def display_fields(self):
        return [
            ("API Key", "api_key", self.mask_secret),
            ("Base URL", "base_url", None),
        ]


# --- Global/Environment Config ---

class LocalServicePorts(BaseModel):
    example_service: int = Field(default=8080, ge=1024, le=65535)


class EnvironmentConfig(BaseModel):
    example_service: ExampleServiceConfig = Field(default_factory=ExampleServiceConfig)
    admin_role_name: Optional[str] = Field(None)
    admin_credential: Optional[str] = Field(None)


class GlobalConfig(BaseModel):
    default_env: Optional[Environment] = Field(None)
    local_service_ports: LocalServicePorts = Field(default_factory=LocalServicePorts)
    environments: Dict[str, EnvironmentConfig] = Field(default_factory=dict)


class ApiClientConfig(BaseModel):
    global_settings: GlobalConfig = Field(default_factory=GlobalConfig)

    def get_env_config(self, env: Environment) -> EnvironmentConfig:
        if env.value not in self.global_settings.environments:
            self.global_settings.environments[env.value] = EnvironmentConfig()
        return self.global_settings.environments[env.value]


# --- Load/Save ---

def get_config_dir_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def get_config_file_path() -> Path:
    return get_config_dir_path() / CONFIG_FILE_NAME


def load_config() -> ApiClientConfig:
    config_path = get_config_file_path()
    if not config_path.exists():
        return ApiClientConfig()
    try:
        with open(config_path, "r") as f:
            config_data = json.load(f)
        return ApiClientConfig(**config_data)
    except Exception:
        return ApiClientConfig()


def save_config(config: ApiClientConfig) -> bool:
    config_dir = get_config_dir_path()
    config_path = get_config_file_path()
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(config.model_dump(), f, indent=4, default=str)
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False
```

---

### 7. `cli.py` - Main Entry Point

```python
import typer
from rich.console import Console
from typing import Optional

from my_api_client.core.service_registry import ServiceRegistry
from my_api_client.utils.env_utils import Environment
from my_api_client.utils.config_utils import load_config, save_config

console = Console()

app = typer.Typer(
    name="my-cli",
    help="CLI for interacting with various API services.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)

# --- IMPORTANT: Import services to trigger auto-registration ---
import my_api_client.example_service.service  # noqa: F401
# import my_api_client.another_service.service  # noqa: F401

# --- Auto-register all services as subcommands ---
for service_class in ServiceRegistry.get_all().values():
    service = service_class()
    app.add_typer(service.create_cli_app(), name=service_class.SERVICE_NAME)


# --- Global Commands ---

@app.command("show-default-env")
def show_default_env():
    """Shows the currently configured default environment."""
    config = load_config()
    if config.global_settings.default_env:
        console.print(f"Default: [green]{config.global_settings.default_env.value}[/green]")
    else:
        console.print("No default environment set.")


@app.command("set-default-env")
def set_default_env(
    env: Environment = typer.Argument(..., help="Environment to set"),
):
    """Sets the default environment."""
    config = load_config()
    config.global_settings.default_env = env
    if save_config(config):
        console.print(f"Default set to: [green]{env.value}[/green]")
    else:
        console.print("[red]Failed to save.[/red]")
        raise typer.Exit(code=1)


@app.command("show-config")
def show_config():
    """Shows all configuration."""
    config = load_config()
    current_env = config.global_settings.default_env

    console.print("[bold]Global Settings:[/bold]")
    console.print(f"  Default Env: {current_env.value if current_env else '[red]Not set[/red]'}")

    if current_env:
        console.print(f"\n[bold]{current_env.value.upper()} Environment:[/bold]")
        for service_class in ServiceRegistry.get_all().values():
            service = service_class()
            service.display_config(console, current_env.value, "  ")


if __name__ == "__main__":
    app()
```

---

## Example Service Implementation

### `example_service/service.py`

```python
from typing import Optional, Type, ClassVar
import typer

from my_api_client.core.base_service import BaseServiceCLI
from my_api_client.example_service.client import ExampleClient
from my_api_client.utils.config_utils import ExampleServiceConfig, load_config
from my_api_client.utils.env_utils import Environment


class ExampleService(BaseServiceCLI):
    """Example service - auto-registered when imported."""

    SERVICE_NAME = "example"
    DISPLAY_NAME = "Example API"
    CONFIG_CLASS: ClassVar[Type[ExampleServiceConfig]] = ExampleServiceConfig

    def get_client(
        self,
        ctx: Optional[typer.Context] = None,
        env: Optional[Environment] = None,
        **kwargs,
    ) -> ExampleClient:
        from typing import cast
        config: ExampleServiceConfig = cast(ExampleServiceConfig, self.get_current_config(env))

        api_key = self.require_not_none(
            config.api_key,
            "API key not configured.",
            "Use 'my-cli example set-api-key <KEY>'"
        )

        effective_env = env or load_config().global_settings.default_env
        validated_env = self.require_not_none(
            effective_env,
            "Environment not set.",
            "Use 'my-cli set-default-env <ENV>'"
        )

        return ExampleClient(
            api_key=api_key,
            env=validated_env,
            verbose=kwargs.get("verbose", False),
        )

    def create_cli_app(self) -> typer.Typer:
        from my_api_client.example_service.cli import example_app
        return example_app
```

### `example_service/client.py`

```python
from typing import Dict, Any, Optional
import requests

from my_api_client.core.base_client import BaseApiClient
from my_api_client.utils.env_utils import Environment


class ExampleClient(BaseApiClient):
    """HTTP client for Example API."""

    ENVIRONMENT_URL_MAP = {
        Environment.prod: "https://api.example.com",
        Environment.staging: "https://staging.api.example.com",
        Environment.dev: "https://dev.api.example.com",
    }

    def __init__(self, api_key: str, **kwargs):
        self.api_key = api_key
        super().__init__(**kwargs)

    def _create_session(self, **auth_params) -> requests.Session:
        session = super()._create_session(**auth_params)
        session.headers["Authorization"] = f"Bearer {self.api_key}"
        return session

    def get_user(self, user_id: str) -> Dict[str, Any]:
        return self._get(f"/users/{user_id}")

    def list_items(self, page: int = 1) -> Dict[str, Any]:
        return self._get("/items", params={"page": page})

    def create_item(self, name: str, value: int) -> Dict[str, Any]:
        return self._post("/items", json_data={"name": name, "value": value})

    @classmethod
    def from_env(cls, **kwargs) -> "ExampleClient":
        import os
        api_key = os.getenv("EXAMPLE_API_KEY")
        env_name = os.getenv("EXAMPLE_ENV", "prod")
        if not api_key:
            raise ValueError("EXAMPLE_API_KEY not set")
        return cls(api_key=api_key, env=Environment[env_name], **kwargs)
```

### `example_service/cli.py`

```python
import typer
from rich.console import Console
from typing import Dict, Any

from my_api_client.core.decorator_utils import service_command
from my_api_client.example_service.service import ExampleService
from my_api_client.example_service.client import ExampleClient
from my_api_client.utils.config_utils import load_config, save_config

console = Console()

example_app = typer.Typer(
    name="example",
    help="Interact with Example API.",
    no_args_is_help=True,
)


# --- Config Commands ---

@example_app.command("set-api-key")
def set_api_key(
    api_key: str = typer.Argument(..., help="API key to set"),
):
    """Set the API key for current environment."""
    config = load_config()
    current_env = config.global_settings.default_env

    if not current_env:
        console.print("[red]No default environment set.[/red]")
        raise typer.Exit(code=1)

    env_config = config.get_env_config(current_env)
    env_config.example_service.api_key = api_key

    if save_config(config):
        console.print(f"[green]API key set for {current_env.value}[/green]")
    else:
        console.print("[red]Failed to save.[/red]")
        raise typer.Exit(code=1)


@example_app.command("show-config")
def show_example_config():
    """Show Example service configuration."""
    config = load_config()
    current_env = config.global_settings.default_env

    if not current_env:
        console.print("[red]No default environment set.[/red]")
        raise typer.Exit(code=1)

    env_config = config.get_env_config(current_env)
    example_config = env_config.example_service

    console.print(f"\n[bold]Example Config ({current_env.value}):[/bold]")
    console.print(f"API Key: {'*' * 8 if example_config.api_key else '[red]Not set[/red]'}")
    console.print(f"Base URL: {example_config.base_url or '[dim]default[/dim]'}")


# --- API Commands (using decorator) ---

@example_app.command("get-user")
@service_command(ExampleService, pass_context=False, verbose=True)
def get_user_cli(client: ExampleClient, user_id: str = typer.Argument(...)) -> Dict[str, Any]:
    """Get user by ID."""
    return client.get_user(user_id)


@example_app.command("list-items")
@service_command(ExampleService, pass_context=False, verbose=True)
def list_items_cli(
    client: ExampleClient,
    page: int = typer.Option(1, "--page", "-p", help="Page number"),
) -> Dict[str, Any]:
    """List items with pagination."""
    return client.list_items(page=page)


@example_app.command("create-item")
@service_command(ExampleService, pass_context=False, verbose=True)
def create_item_cli(
    client: ExampleClient,
    name: str = typer.Argument(..., help="Item name"),
    value: int = typer.Argument(..., help="Item value"),
) -> Dict[str, Any]:
    """Create a new item."""
    return client.create_item(name=name, value=value)
```

### `example_service/models.py` (Optional - for complex validation)

```python
from pydantic import BaseModel, Field, validator
from typing import Optional, List


class CreateItemRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    value: int = Field(..., ge=0)
    tags: List[str] = Field(default_factory=list)

    @validator("tags")
    def validate_tags(cls, v):
        return [tag.strip().lower() for tag in v if tag.strip()]

    def to_api_payload(self):
        return {"name": self.name, "value": self.value, "tags": self.tags}


class ItemResponse(BaseModel):
    id: str
    name: str
    value: int
    created_at: Optional[str] = None
```

---

## `pyproject.toml` Setup

```toml
[project]
name = "my-api-client"
version = "0.1.0"
description = "CLI for multiple API services"
requires-python = ">=3.10"
dependencies = [
    "typer>=0.9.0",
    "rich>=13.0.0",
    "pydantic>=2.0.0",
    "requests>=2.28.0",
]

[project.scripts]
my-cli = "my_api_client.cli:app"

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
```

---

## Package `__init__.py` (For Script Usage)

```python
"""Easy imports for scripts and notebooks."""

from my_api_client.example_service.client import ExampleClient
from my_api_client.utils.env_utils import Environment

__all__ = ["ExampleClient", "Environment"]
```

---

## Key Patterns Summary

### 1. Auto-Registration Flow
```
1. Service class defined with SERVICE_NAME, DISPLAY_NAME, CONFIG_CLASS
2. __init_subclass__ validates and registers with ServiceRegistry
3. Main CLI imports service modules (triggers registration)
4. CLI loops through ServiceRegistry.get_all() to add subcommands
```

### 2. @service_command Decorator
```
1. Extracts --env, --pretty, --verbose from CLI args
2. Creates service instance
3. Calls service.get_client(env=..., verbose=...)
4. Passes client to decorated function
5. Calls service.output_json(result, pretty=...)
```

### 3. Environment-Specific Config
```
~/.config/my_api_client/config.json:
{
  "global_settings": {
    "default_env": "prod",
    "environments": {
      "prod": { "example_service": { "api_key": "..." } },
      "dev": { "example_service": { "api_key": "..." } }
    }
  }
}
```

### 4. Adding a New Service

1. Create `new_service/` directory with:
   - `service.py` - extends `BaseServiceCLI` with required class vars
   - `client.py` - extends `BaseApiClient` with `ENVIRONMENT_URL_MAP`
   - `cli.py` - creates `typer.Typer` with commands
   - `models.py` - Pydantic models for validation (optional)

2. Add config class to `utils/config_utils.py`:
   ```python
   class NewServiceConfig(BaseServiceConfig):
       api_key: Optional[str] = ...
   ```

3. Add to `EnvironmentConfig`:
   ```python
   new_service: NewServiceConfig = Field(default_factory=NewServiceConfig)
   ```

4. Import in `cli.py`:
   ```python
   import my_api_client.new_service.service  # noqa: F401
   ```

5. Service automatically appears in CLI!

---

## Benefits

- **70% less boilerplate** - decorators and base classes handle common patterns
- **Type safety** - Pydantic validation throughout
- **Environment isolation** - each env has separate config
- **Auto-registration** - no manual CLI wiring
- **Script-friendly** - `from my_api_client import ExampleClient`
- **Consistent UX** - all services have same CLI patterns
