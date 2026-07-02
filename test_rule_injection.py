"""Test file to verify rule injection in reviews."""


class UserService:
    """User service without dependency injection."""

    def __init__(self):
        self.db = DatabaseConnection()
        self.cache = RedisCache()
        self.logger = Logger()

    def get_user(self, user_id):
        return self.db.query(f"SELECT * FROM users WHERE id = {user_id}")

    def create_user(self, name, email):
        user = {"name": name, "email": email}
        self.db.insert("users", user)
        self.cache.invalidate("users")
        return user
