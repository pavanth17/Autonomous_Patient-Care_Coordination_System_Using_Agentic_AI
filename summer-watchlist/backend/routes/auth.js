const express = require('express');
const router = express.Router();
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const User = require('../models/User');
const auth = require('../middleware/auth');

// Get all users (profiles)
router.get('/profiles', async (req, res) => {
  try {
    const users = await User.find({});
    res.json(users);
  } catch (err) {
    res.status(500).json({ message: 'Server Error' });
  }
});

// Login (just select profile)
router.post('/login', async (req, res) => {
  try {
    const { username } = req.body;
    const user = await User.findOne({ username });
    if (!user) return res.status(400).json({ message: 'User not found' });

    const token = jwt.sign({ _id: user._id, username: user.username }, process.env.JWT_SECRET || 'fallback_secret');
    res.json({ token, user: { id: user._id, username: user.username, color: user.color, avatarUrl: user.avatarUrl } });
  } catch (err) {
    res.status(500).json({ message: 'Server Error' });
  }
});

// Get current user
router.get('/me', auth, async (req, res) => {
  try {
    const user = await User.findById(req.user._id);
    res.json(user);
  } catch (err) {
    res.status(500).json({ message: 'Server Error' });
  }
});

module.exports = router;
