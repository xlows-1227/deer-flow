"""User repository interface for abstracting database operations."""

from abc import ABC, abstractmethod

from app.gateway.auth.models import User


class UserNotFoundError(LookupError):
    """Raised when a user repository operation targets a non-existent row.

    Subclass of :class:`LookupError` so callers that already catch
    ``LookupError`` for "missing entity" can keep working unchanged,
    while specific call sites can pin to this class to distinguish
    "concurrent delete during update" from other lookups.
    """


class UserRepository(ABC):
    """Abstract interface for user data storage.

    Implement this interface to support different storage backends
    (SQLite)
    """

    @abstractmethod
    async def create_user(self, user: User) -> User:
        """Create a new user.

        Args:
            user: User object to create

        Returns:
            Created User with ID assigned

        Raises:
            ValueError: If email already exists
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_id(self, user_id: str) -> User | None:
        """Get user by ID.

        Args:
            user_id: User UUID as string

        Returns:
            User if found, None otherwise
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_email(self, email: str) -> User | None:
        """Get user by email.

        Args:
            email: User email address

        Returns:
            User if found, None otherwise
        """
        raise NotImplementedError

    @abstractmethod
    async def update_user(self, user: User) -> User:
        """Update an existing user.

        Args:
            user: User object with updated fields

        Returns:
            Updated User

        Raises:
            UserNotFoundError: If no row exists for ``user.id``. This is
                a hard failure (not a no-op) so callers cannot mistake a
                concurrent-delete race for a successful update.
        """
        raise NotImplementedError

    @abstractmethod
    async def count_users(self) -> int:
        """Return total number of registered users."""
        raise NotImplementedError

    @abstractmethod
    async def count_admin_users(self) -> int:
        """Return number of users with system_role == 'admin'."""
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        """Get user by OAuth provider and ID.

        Args:
            provider: OAuth provider name (e.g. 'github', 'google')
            oauth_id: User ID from OAuth provider

        Returns:
            User if found, None otherwise
        """
        raise NotImplementedError

    @abstractmethod
    async def list_users(self) -> list[User]:
        """Return all active (non-deleted) users ordered by email ascending.

        Used for sharee picker UIs and admin user listings.  Soft-deleted
        accounts are excluded so they never surface in share dialogs or
        admin listings.  Callers should further filter the result set when
        showing only users that are eligible for a specific share
        relationship.
        """
        raise NotImplementedError

    @abstractmethod
    async def soft_delete_user(self, user_id: str) -> User | None:
        """Mark a user as soft-deleted (deleted=True, deleted_at=now).

        Also bumps ``token_version`` so the user's outstanding JWTs stop
        validating immediately.  The row itself is kept so historical
        references (skill shares, thread ownership) stay resolvable.

        Args:
            user_id: User UUID as string

        Returns:
            Updated User, or None when no row exists for ``user_id``
        """
        raise NotImplementedError
