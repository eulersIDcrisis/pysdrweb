"""auth.py.

Authentication utilities for RequestHandlers.
"""
import base64
from functools import wraps


class ParseError(Exception):
    """Error denoting an issue parsing settings."""


class NotAuthorized(Exception):
    """Exception denoting the caller is not authorized."""


class MissingBasicAuth(NotAuthorized):
    """Exception denoting that authentication is missing.

    Subclass of 'NotAuthorized' to distinguish between the handler sending
    response codes of either 401 or 403.
    """


class BaseAuthManager(object):
    """Base class for authentication.

    For convenience, this can also be used as a manager to deny every
    request.
    """

    def __init__(self, ignore_on_read=True):
        self._ignore_on_read = ignore_on_read

    @property
    def ignore_on_read(self):
        """Return whether to ignore authentication for 'read' requests."""
        return self._ignore_on_read

    def authenticate(self, token):
        """Return the user this request is authenticated for.

        If not authenticated by this manager, this should raise some form
        of 'NotAuthorized' exception.

        NOTE: The default/base implementation will raise NotAuthorized()
        for every request as a convenience.
        """
        raise NotAuthorized("Request not authorized!")


class AllAccessAuthManager(BaseAuthManager):
    """AuthManager that permits ALL access.

    WARNING: Only use this if you do NOT care about authentication!
    """

    def authenticate(self, req_handler):
        """Authenticate all requests and ignore any checks."""
        return None


class BasicAuthManager(BaseAuthManager):
    """AuthManager for 'Basic' username/password authentication."""

    HEADER_PREFIX = 'Basic '
    """Header prefix for the 'Authorization:' header with this style auth."""

    def __init__(self, user_password_mapping=None, ignore_on_read=True):
        super(BasicAuthManager, self).__init__(ignore_on_read=ignore_on_read)
        if isinstance(user_password_mapping, dict):
            self.user_password_mapping = user_password_mapping
        else:
            self.user_password_mapping = dict()

    def authenticate(self, req_handler):
        """Authenticate this request by checking for the 'Basic' header."""
        # Check for the authentication header.
        header = req_handler.request.headers.get('Authorization', None)
        if not header:
            raise MissingBasicAuth('Missing authentication credentials!')
        if not header.startswith(BasicAuthManager.HEADER_PREFIX):
            raise MissingBasicAuth('Invalid authentication type!')
        try:
            # Decode the header as a string.
            decoded = base64.b64decode(
                # Drop the 'Basic ' portion of the header.
                header[len(BasicAuthManager.HEADER_PREFIX):]
            ).decode('utf-8')
            user, password = decoded.split(':', 1)

            expected_password = self.user_password_mapping.get(user)
            if expected_password is None or expected_password != password:
                raise MissingBasicAuth(
                    'Invalid password for user: {}'.format(user))
            return user
        except NotAuthorized:
            # Exception message is already okay to raise. Also will raise
            # MissingBasicAuth implicitly as well.
            raise
        except Exception as exc:
            logger.error('Error decoding token: %s', exc)
            raise NotAuthorized()


def parse_auth_manager_from_options(options_dict):
    """Parse out an AuthManager from the given options."""
    try:
        auth_dict = options_dict.get('auth', dict(type='null'))
        auth_type = auth_dict.get('type', 'null').lower()
        ignore_on_read = auth_dict.get('ignore_on_read', True)
        if auth_type in ['none', 'null']:
            # Do not authenticate requests for these types.
            return AllAccessAuthManager()
        if auth_type == 'basic':
            # Parse the options to create the BasicAuthManager.
            user_mapping = {}
            for user, password in auth_dict.get('users', dict()).items():
                if not isinstance(user, str) or not isinstance(password, str):
                    raise ValueError(
                        "Usernames and passwords MUST be strings!")
                user_mapping[user] = password
            return BasicAuthManager(user_mapping, ignore_on_read)

        raise Exception("Invalid auth_type given: {}".format(auth_type))
    except Exception as exc:
        raise ParseError(str(exc)) from exc


#
# RequestHandler Method Decorator Utilities
#
def authenticated(readonly=True):
    def wrapper(func):
        @wraps(func)
        def inner_wrapper(handler, *args, **kwargs):
            try:
                context = handler.get_context()
                manager = context.auth_manager

                # Require authentication if the request is not readonly
                # OR if the auth_manager wants auth for read requests too.
                if not readonly or not manager.ignore_on_read:
                    manager.authenticate(handler)
            except MissingBasicAuth:
                handler.set_header(
                    'WWW-Authenticate',
                    'Basic realm="FM SDR Server"')
                handler.send_status(401, 'Missing authentication!')
                return
            except NotAuthorized:
                handler.send_status(403, 'Authentication failed!')
                return
            except Exception:
                logger.exception("Unexpected error processing auth!")
                handler.send_status(500, 'Internal Server Error')
                return

            # Authentication succeeded. Call the main function.
            return func(handler, *args, **kwargs)
        return inner_wrapper
    return wrapper
