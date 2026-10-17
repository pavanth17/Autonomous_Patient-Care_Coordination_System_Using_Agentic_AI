const express = require('express');
const router = express.Router();
const WatchlistItem = require('../models/WatchlistItem');
const auth = require('../middleware/auth');

// Get all shared items
router.get('/', auth, async (req, res) => {
  try {
    const items = await WatchlistItem.find({}).sort({ createdAt: -1 });
    res.json(items);
  } catch (err) {
    res.status(500).json({ message: 'Server Error' });
  }
});

// Add a new item
router.post('/', auth, async (req, res) => {
  try {
    const newItem = new WatchlistItem(req.body);
    const item = await newItem.save();
    res.json(item);
  } catch (err) {
    res.status(500).json({ message: 'Server Error', error: err.message });
  }
});

// Update an item
router.put('/:id', auth, async (req, res) => {
  try {
    let item = await WatchlistItem.findById(req.params.id);
    if (!item) return res.status(404).json({ message: 'Item not found' });

    item = await WatchlistItem.findByIdAndUpdate(req.params.id, { $set: req.body }, { new: true });
    res.json(item);
  } catch (err) {
    res.status(500).json({ message: 'Server Error' });
  }
});

// Delete an item
router.delete('/:id', auth, async (req, res) => {
  try {
    const item = await WatchlistItem.findById(req.params.id);
    if (!item) return res.status(404).json({ message: 'Item not found' });

    await WatchlistItem.findByIdAndDelete(req.params.id);
    res.json({ message: 'Item removed' });
  } catch (err) {
    res.status(500).json({ message: 'Server Error' });
  }
});

// Toggle watched status for the current user
router.post('/:id/toggle-status', auth, async (req, res) => {
  try {
    const item = await WatchlistItem.findById(req.params.id);
    if (!item) return res.status(404).json({ message: 'Item not found' });

    const userId = req.user._id;
    const isWatched = item.watchedBy.includes(userId);

    if (isWatched) {
      item.watchedBy = item.watchedBy.filter(id => id.toString() !== userId.toString());
    } else {
      item.watchedBy.push(userId);
    }

    await item.save();
    res.json(item);
  } catch (err) {
    res.status(500).json({ message: 'Server Error' });
  }
});

module.exports = router;
