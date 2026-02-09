"""
Create a VMcard pool
"""
import logging
import threading
from .vmcard import VMCard
from .constants import VmStatus

class VMCardPool:
    """
    Manages a pool of VMCard widgets to avoid remounting/unmounting
    when changing pages.
    """
    def __init__(self, pool_size: int):
        if pool_size < 0:
            raise ValueError(f"pool_size must be non-negative, got {pool_size}")
        self.pool_size = pool_size
        self.available_cards: list[VMCard] = []
        self.active_cards: dict[str, VMCard] = {}  # uuid -> card
        self.last_page_order: list[str] = []  # Track last page's UUID order
        self.lock = threading.Lock()

    def prefill_pool(self) -> None:
        """Prefill the pool with cards up to pool_size."""
        with self.lock:
            current_count = len(self.available_cards)
            if current_count < self.pool_size:
                to_create = self.pool_size - current_count
                logging.debug(f"Prefilling pool with {to_create} cards")
                for _ in range(to_create):
                    self.available_cards.append(VMCard(is_selected=False))

    def get_or_create_card(self, uuid: str) -> VMCard:
        """Get a card from the pool or create a new one."""
        if not uuid:
            raise ValueError("UUID cannot be None or empty")
        with self.lock:
            # If we already have an active card for this UUID, return it
            if uuid in self.active_cards:
                return self.active_cards[uuid]

            # Try to reuse a card from the pool
            if self.available_cards:
                card = self.available_cards.pop()
                logging.debug(f"Reusing card from pool for {uuid}")
            else:
                # Create new card if pool is empty
                card = VMCard(is_selected=False)
                logging.debug(f"Creating new card for {uuid}")

            self.active_cards[uuid] = card
            return card

    def release_card(self, uuid: str) -> None:
        """Release a card back to the pool."""
        if not uuid:
            raise ValueError("UUID cannot be None or empty")

        with self.lock:
            if uuid not in self.active_cards:
                logging.warning(f"Attempted to release card {uuid} that is not active")
                return

            card = self.active_cards.pop(uuid)

            # Reset card state before returning to pool
            try:
                card.reset_for_reuse()
            except Exception as e:
                logging.error(f"Error resetting card {uuid} for reuse: {e}")

            card.vm = None
            card.conn = None
            card.name = ""
            card.status = VmStatus.DEFAULT
            card.cpu = 0
            card.memory = 0
            card.is_selected = False

            if len(self.available_cards) < self.pool_size:
                self.available_cards.append(card)
                logging.debug(f"Released card {uuid} to pool (pool size: {len(self.available_cards)}/{self.pool_size})")
            else:
                # Pool is full, actually remove the card
                try:
                    if hasattr(card, 'is_mounted') and card.is_mounted:
                        card.remove()
                    logging.debug(f"Pool full, removed card {uuid}")
                except Exception as e:
                    logging.error(f"Error removing card {uuid} when pool full: {e}")

    def clear_pool(self) -> None:
        """Clear the entire pool and release all resources."""
        with self.lock:
            # Clean up all active cards first
            active_uuids = list(self.active_cards.keys())

        # Release outside lock to avoid potential deadlocks
        for uuid in active_uuids:
            try:
                self.release_card(uuid)
            except Exception as e:
                logging.error(f"Error releasing active card {uuid} during pool clear: {e}")

        # Clean up all cards in pool
        with self.lock:
            # Clean up all cards in pool
            for card in self.available_cards:
                try:
                    if hasattr(card, 'is_mounted') and card.is_mounted:
                        card.remove()
                except Exception as e:
                    logging.error(f"Error removing card during pool clear: {e}")

            self.available_cards.clear()
            self.active_cards.clear()
            self.last_page_order.clear()

    def get_pool_stats(self) -> dict[str, int]:
        """Returns statistics about the pool state."""
        with self.lock:
            return {
                'pool_size': self.pool_size,
                'active_cards': len(self.active_cards),
                'available_cards': len(self.available_cards),
                'total_cards': len(self.active_cards) + len(self.available_cards)
            }
