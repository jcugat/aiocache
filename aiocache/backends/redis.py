import asyncio
import itertools
import aioredis


class RedisBackend:

    pools = {}
    DEFAULT_ENDPOINT = "127.0.0.1"
    DEFAULT_PORT = 6379
    DEFAULT_DB = 0
    DEFAULT_PASSWORD = None
    DEFAULT_POOL_MIN_SIZE = 1
    DEFAULT_POOL_MAX_SIZE = 10

    def __init__(
            self, endpoint=None, port=None, db=None, password=None, pool_min_size=None,
            pool_max_size=None, loop=None, **kwargs):
        super().__init__(**kwargs)
        self.endpoint = endpoint if endpoint is not None else self.DEFAULT_ENDPOINT
        self.port = port if port is not None else self.DEFAULT_PORT
        self.db = db if db is not None else self.DEFAULT_DB
        self.password = password if password is not None else self.DEFAULT_PASSWORD
        self.pool_min_size = pool_min_size if pool_min_size is not None \
            else self.DEFAULT_POOL_MIN_SIZE
        self.pool_max_size = pool_max_size if pool_max_size is not None \
            else self.DEFAULT_POOL_MAX_SIZE
        self._loop = loop or asyncio.get_event_loop()

    @classmethod
    def set_defaults(
            cls, endpoint=DEFAULT_ENDPOINT, port=DEFAULT_PORT, db=DEFAULT_DB,
            password=DEFAULT_PASSWORD, pool_min_size=DEFAULT_POOL_MIN_SIZE,
            pool_max_size=DEFAULT_POOL_MAX_SIZE, **kwargs):
        try:
            super().set_defaults(**kwargs)
        except AttributeError:
            pass
        cls.DEFAULT_ENDPOINT = endpoint
        cls.DEFAULT_PORT = port
        cls.DEFAULT_DB = db
        cls.DEFAULT_PASSWORD = password
        cls.DEFAULT_POOL_MAX_SIZE = pool_max_size
        cls.DEFAULT_POOL_MIN_SIZE = pool_min_size

    async def _get(self, key, encoding="utf-8"):
        """
        Get a value from the cache

        :param key: str
        :returns: obj in key if found else None
        """

        with await self._connect() as redis:
            return await redis.get(key, encoding=encoding)

    async def _multi_get(self, keys, encoding="utf-8"):
        """
        Get multi values from the cache. For each key not found it returns a None

        :param key: str
        :returns: list of obj for each key found, else if not found
        """
        with await self._connect() as redis:
            return await redis.mget(*keys, encoding=encoding)

    async def _set(self, key, value, ttl=None):
        """
        Stores the value in the given key.

        :param key: str
        :param value: obj
        :param ttl: int
        :returns: True
        """
        with await self._connect() as redis:
            return await redis.set(key, value, expire=ttl)

    async def _multi_set(self, pairs, ttl=None):
        """
        Stores multiple values in the given keys.

        :param pairs: list of two element iterables. First is key and second is value
        :param ttl: int
        :returns: True
        """
        ttl = ttl or 0

        with await self._connect() as redis:
            transaction = redis.multi_exec()
            flattened = list(itertools.chain.from_iterable(
                (key, value) for key, value in pairs))
            transaction.mset(*flattened)
            if ttl > 0:
                for key in flattened[::2]:
                    transaction.expire(key, timeout=ttl)

            await transaction.execute()

        return True

    async def _add(self, key, value, ttl=None):
        """
        Stores the value in the given key. Raises an error if the
        key already exists.

        :param key: str
        :param value: obj
        :param ttl: int
        :returns: True if key is inserted
        :raises: Value error if key already exists
        """

        with await self._connect() as redis:
            was_set = await redis.set(key, value, expire=ttl, exist=redis.SET_IF_NOT_EXIST)
            if not was_set:
                raise ValueError(
                    "Key {} already exists, use .set to update the value".format(key))
            return was_set

    async def _exists(self, key):
        """
        Check key exists in the cache.

        :param key: str key to check
        :returns: True if key exists otherwise False
        """
        with await self._connect() as redis:
            exists = await redis.exists(key)
            return True if exists > 0 else False

    async def _increment(self, key, delta):
        with await self._connect() as redis:
            try:
                return await redis.incrby(key, delta)
            except aioredis.errors.ReplyError:
                raise TypeError("Value is not an integer") from None

    async def _expire(self, key, ttl):
        """
        Expire the given key in ttl seconds. If ttl is 0, remove the expiration

        :param key: str key to expire
        :param ttl: int number of seconds for expiration. If 0, ttl is disabled
        :returns: True if set, False if key is not found
        """
        with await self._connect() as redis:
            if ttl == 0:
                return await redis.persist(key)
            return await redis.expire(key, ttl)

    async def _delete(self, key):
        """
        Deletes the given key.

        :param key: Key to be deleted
        :returns: int number of deleted keys
        """
        with await self._connect() as redis:
            return await redis.delete(key)

    async def _clear(self, namespace=None):
        """
        Deletes the given key.

        :param namespace:
        :returns: True
        """
        with await self._connect() as redis:
            if namespace:
                keys = await redis.keys("{}:*".format(namespace))
                await redis.delete(*keys)
            else:
                await redis.flushdb()
        return True

    async def _raw(self, command, *args, encoding="utf-8", **kwargs):
        """
        Executes a raw command using the underlying client of aioredis. It's under
        the developer responsibility to send the needed args and kwargs.

        :param command: str command to execute
        """
        if command in ["get", "mget"]:
            kwargs["encoding"] = encoding
        with await self._connect() as redis:
            return await getattr(redis, command)(*args, **kwargs)

    def get_pool(self):
        pool_key = "{}{}{}{}{}".format(
            self.endpoint, self.port,
            self.db, self.password, id(self._loop))
        return pool_key, RedisBackend.pools.get(pool_key)

    async def _connect(self):
        pool_key, pool = self.get_pool()

        if pool is None:
            pool = await aioredis.create_pool(
                (self.endpoint, self.port),
                db=self.db,
                password=self.password,
                loop=self._loop,
                encoding="utf-8",
                minsize=self.pool_min_size,
                maxsize=self.pool_max_size)
            RedisBackend.pools[pool_key] = pool

        return await pool
