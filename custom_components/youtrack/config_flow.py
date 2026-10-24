"""Config flow for the YouTrack integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import probatio
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import YouTrackClient, normalize_base_url
from .const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, DOMAIN
from .exceptions import (
    YouTrackAuthError,
    YouTrackConnectionError,
    YouTrackError,
    YouTrackPermissionError,
    YouTrackResponseError,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        probatio.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        probatio.Required(CONF_VERIFY_SSL, default=True): BooleanSelector(),
    }
)


async def validate_connection(
    hass: HomeAssistant, url: str, token: str, *, verify_ssl: bool
) -> str | None:
    """Check URL and token; return an error key or None if everything works."""
    client = YouTrackClient(
        async_get_clientsession(hass, verify_ssl=verify_ssl), url, token, verify_ssl=verify_ssl
    )
    try:
        await client.get_server_info()
        await client.get_current_user()
    except YouTrackAuthError:
        return "invalid_auth"
    except YouTrackPermissionError:
        return "no_permission"
    except YouTrackResponseError:
        return "invalid_response"
    except YouTrackConnectionError:
        return "cannot_connect"
    except YouTrackError:
        return "unknown"
    return None


class YouTrackConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup of a YouTrack instance."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for URL and token and verify them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_base_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                error = await validate_connection(
                    self.hass,
                    url,
                    user_input[CONF_API_TOKEN],
                    verify_ssl=user_input[CONF_VERIFY_SSL],
                )
                if error is None:
                    await self.async_set_unique_id(url)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=urlsplit(url).netloc,
                        data={**user_input, CONF_URL: url},
                    )
                errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )
