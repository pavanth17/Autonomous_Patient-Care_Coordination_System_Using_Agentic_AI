import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import axios from 'axios';
import { Search, X, Loader2 } from 'lucide-react';

// Mock function if OMDB key is missing
const mockOmdbFetch = async (query) => {
  return new Promise(resolve => {
    setTimeout(() => {
      resolve({
        Title: query,
        Year: '2023',
        Type: 'movie',
        Poster: `https://image.tmdb.org/t/p/w500/8cdWjvZQUExUUTzyp4t6EDMubfO.jpg`, // Deadpool mock poster
        Plot: `A great ${query} movie that you should definitely watch.`
      });
    }, 1000);
  });
};

const AddMediaModal = ({ isOpen, onClose, onAdd }) => {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!query) return;
    
    setLoading(true);
    setError('');
    setResult(null);

    try {
      // In a real app, replace with actual OMDB API: 
      // const res = await axios.get(`https://www.omdbapi.com/?t=${query}&apikey=YOUR_KEY`);
      // For now, using mock fetch
      const data = await mockOmdbFetch(query);
      
      if (data.Error) {
        setError(data.Error);
      } else {
        setResult(data);
      }
    } catch (err) {
      setError('Failed to fetch data');
    } finally {
      setLoading(false);
    }
  };

  const handleAdd = async () => {
    if (!result) return;
    
    try {
      const newItem = {
        title: result.Title,
        type: result.Type === 'movie' ? 'movie' : 'series',
        poster_url: result.Poster !== 'N/A' ? result.Poster : '',
        description: result.Plot,
        ott_platform: 'Netflix', // Default mock
        watchedBy: []
      };

      const token = localStorage.getItem('token');
      const res = await axios.post('http://localhost:5000/api/watchlist', newItem, {
        headers: { Authorization: `Bearer ${token}` }
      });
      
      onAdd(res.data);
      onClose();
      setQuery('');
      setResult(null);
    } catch (err) {
      setError('Failed to add to watchlist');
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
          />
          
          <motion.div 
            initial={{ opacity: 0, scale: 0.9, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.9, y: 20 }}
            className="glass-panel w-full max-w-lg p-6 relative z-10"
          >
            <button 
              onClick={onClose}
              className="absolute top-4 right-4 text-textMuted hover:text-white transition-colors"
            >
              <X className="w-6 h-6" />
            </button>

            <h2 className="text-2xl font-bold mb-6 text-transparent bg-clip-text bg-gradient-to-r from-primary to-neonGreen">
              Add to WatchVibe
            </h2>

            <form onSubmit={handleSearch} className="flex gap-2 mb-6">
              <input 
                type="text" 
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search movie, series, anime..."
                className="input-field flex-grow"
              />
              <button 
                type="submit" 
                disabled={loading}
                className="bg-primary text-black p-3 rounded-xl hover:bg-[#4facfe] transition-colors shadow-neon-primary disabled:opacity-50"
              >
                {loading ? <Loader2 className="w-6 h-6 animate-spin" /> : <Search className="w-6 h-6" />}
              </button>
            </form>

            {error && <p className="text-neonRed mb-4">{error}</p>}

            {result && (
              <motion.div 
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex gap-4 bg-white/5 p-4 rounded-xl border border-white/10"
              >
                <img 
                  src={result.Poster !== 'N/A' ? result.Poster : 'https://via.placeholder.com/150'} 
                  alt={result.Title} 
                  className="w-24 h-36 object-cover rounded-lg shadow-glass"
                />
                <div className="flex flex-col justify-between">
                  <div>
                    <h3 className="font-bold text-lg">{result.Title} <span className="text-sm text-textMuted font-normal">({result.Year})</span></h3>
                    <p className="text-xs text-primary uppercase tracking-wider mb-2">{result.Type}</p>
                    <p className="text-sm text-textMuted line-clamp-3">{result.Plot}</p>
                  </div>
                  <button 
                    onClick={handleAdd}
                    className="btn-primary w-full mt-4 text-sm py-2"
                  >
                    Add to Shared List
                  </button>
                </div>
              </motion.div>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
};

export default AddMediaModal;
