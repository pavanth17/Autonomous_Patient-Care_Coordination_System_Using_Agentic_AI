const mongoose = require('mongoose');

const watchlistItemSchema = new mongoose.Schema({
  title: { type: String, required: true },
  type: { type: String, enum: ['movie', 'series', 'anime'], required: true },
  genre: { type: String },
  poster_url: { type: String },
  backdrop_url: { type: String },
  description: { type: String },
  ott_platform: { type: String },
  mood: { type: String },
  watchedBy: [{ type: mongoose.Schema.Types.ObjectId, ref: 'User' }],
  total_episodes: { type: Number, default: 1 },
  rating: { type: Number, min: 0, max: 10 },
  notes: { type: String },
  createdAt: { type: Date, default: Date.now }
});

module.exports = mongoose.model('WatchlistItem', watchlistItemSchema);
