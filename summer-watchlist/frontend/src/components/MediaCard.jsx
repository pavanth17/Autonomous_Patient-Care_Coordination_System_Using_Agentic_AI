import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import axios from 'axios';
import confetti from 'canvas-confetti';

const MediaCard = ({ item, profiles, currentUser, onUpdate }) => {
  const [isFlipped, setIsFlipped] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);

  // Status computation
  const watchedCount = item.watchedBy ? item.watchedBy.length : 0;
  const isCompleted = watchedCount === profiles.length && profiles.length > 0;
  const isWatching = watchedCount > 0 && watchedCount < profiles.length;
  
  let borderColor = 'border-neonRed';
  let shadowGlow = 'hover:shadow-neon-red';
  if (isCompleted) {
    borderColor = 'border-neonGreen';
    shadowGlow = 'hover:shadow-neon-green';
  } else if (isWatching) {
    borderColor = 'border-neonYellow';
    shadowGlow = 'hover:shadow-neon-yellow';
  }

  const handleToggleWatch = async (e, userId) => {
    e.stopPropagation();
    if (userId !== currentUser._id) {
      // You can only toggle your own status, unless we want anyone to toggle anyone's
      // Let's restrict it to current user for realism
      return; 
    }
    
    setIsUpdating(true);
    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`http://localhost:5000/api/watchlist/${item._id}/toggle-status`, {}, {
        headers: { Authorization: `Bearer ${token}` }
      });
      
      const newWatchedCount = res.data.watchedBy.length;
      if (newWatchedCount === profiles.length && watchedCount < profiles.length) {
        // Confetti!
        confetti({
          particleCount: 100,
          spread: 70,
          origin: { y: 0.6 },
          colors: ['#00f2fe', '#fe0979', '#00ff88']
        });
      }
      
      onUpdate(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <div 
      className={`glass-card group w-full aspect-[2/3] cursor-pointer perspective-1000 border-2 transition-all duration-500 ${borderColor} ${shadowGlow}`}
      onClick={() => setIsFlipped(!isFlipped)}
    >
      <AnimatePresence initial={false} mode="wait">
        {!isFlipped ? (
          <motion.div
            key="front"
            initial={{ rotateY: 180, opacity: 0 }}
            animate={{ rotateY: 0, opacity: 1 }}
            exit={{ rotateY: -180, opacity: 0 }}
            transition={{ duration: 0.4 }}
            className="w-full h-full relative"
          >
            <img 
              src={item.poster_url || 'https://via.placeholder.com/400x600?text=No+Poster'} 
              alt={item.title} 
              className="w-full h-full object-cover rounded-xl"
            />
            <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/30 to-transparent flex flex-col justify-end p-4">
              <h3 className="text-xl font-bold text-white mb-2 truncate drop-shadow-md">{item.title}</h3>
              <div className="flex justify-between items-center">
                <span className="text-xs uppercase font-bold tracking-wider text-neonPrimary bg-white/10 px-2 py-1 rounded backdrop-blur-md">
                  {item.type}
                </span>
                {item.ott_platform && (
                  <span className="text-xs font-bold text-white bg-black/50 px-2 py-1 rounded border border-white/20">
                    {item.ott_platform}
                  </span>
                )}
              </div>
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="back"
            initial={{ rotateY: 180, opacity: 0 }}
            animate={{ rotateY: 0, opacity: 1 }}
            exit={{ rotateY: -180, opacity: 0 }}
            transition={{ duration: 0.4 }}
            className="w-full h-full p-6 flex flex-col bg-black/80 rounded-xl"
          >
            <h3 className="text-xl font-bold text-neonPrimary mb-2 border-b border-white/10 pb-2">{item.title}</h3>
            <p className="text-sm text-textMuted flex-grow overflow-y-auto mb-4 custom-scrollbar">
              {item.description || 'No description available.'}
            </p>
            
            <div className="mt-auto">
              <p className="text-xs text-center text-textMuted mb-2 uppercase tracking-widest">Watch Status</p>
              <div className="flex justify-center gap-4 bg-white/5 p-3 rounded-xl border border-white/10">
                {profiles.map(profile => {
                  const hasWatched = item.watchedBy && item.watchedBy.includes(profile._id);
                  const isMe = profile._id === currentUser._id;
                  
                  return (
                    <button
                      key={profile._id}
                      onClick={(e) => handleToggleWatch(e, profile._id)}
                      disabled={!isMe || isUpdating}
                      className={`relative w-10 h-10 rounded-full border-2 transition-all duration-300 overflow-hidden ${
                        hasWatched ? 'scale-110 shadow-neon-green' : 'opacity-50 grayscale hover:grayscale-0'
                      } ${!isMe && 'cursor-default'} ${isUpdating && isMe && 'animate-pulse'}`}
                      style={{ borderColor: hasWatched ? '#00ff88' : 'transparent' }}
                      title={`${profile.username} ${hasWatched ? 'watched' : "hasn't watched"}`}
                    >
                      <img src={profile.avatarUrl} alt={profile.username} className="w-full h-full object-cover" />
                      {hasWatched && (
                        <div className="absolute inset-0 bg-green-500/20 flex items-center justify-center">
                          <svg className="w-6 h-6 text-[#00ff88] drop-shadow-md" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7"></path></svg>
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default MediaCard;
