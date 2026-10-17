import React, { useState, useEffect, useContext } from 'react';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import { Plus, LogOut, Tv, Play, CheckCircle } from 'lucide-react';
import { AuthContext } from '../contexts/AuthContext';
import MediaCard from '../components/MediaCard';
import AddMediaModal from '../components/AddMediaModal';

const Dashboard = () => {
  const { user, logout } = useContext(AuthContext);
  const [items, setItems] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('all');

  const fetchData = async () => {
    try {
      const token = localStorage.getItem('token');
      const [itemsRes, profilesRes] = await Promise.all([
        axios.get('http://localhost:5000/api/watchlist', { headers: { Authorization: `Bearer ${token}` } }),
        axios.get('http://localhost:5000/api/auth/profiles')
      ]);
      setItems(itemsRes.data);
      setProfiles(profilesRes.data);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleItemUpdate = (updatedItem) => {
    setItems(prev => prev.map(item => item._id === updatedItem._id ? updatedItem : item));
  };

  const handleItemAdd = (newItem) => {
    setItems(prev => [newItem, ...prev]);
  };

  const numUsers = profiles.length || 3;
  
  // Computed stats
  const totalItems = items.length;
  const completedItems = items.filter(i => i.watchedBy && i.watchedBy.length === numUsers);
  const watchingItems = items.filter(i => i.watchedBy && i.watchedBy.length > 0 && i.watchedBy.length < numUsers);
  const notStartedItems = items.filter(i => !i.watchedBy || i.watchedBy.length === 0);

  const completionPercent = totalItems === 0 ? 0 : Math.round((completedItems.length / totalItems) * 100);

  let displayedItems = items;
  if (activeTab === 'movies') displayedItems = items.filter(i => i.type === 'movie');
  if (activeTab === 'series') displayedItems = items.filter(i => i.type === 'series');
  if (activeTab === 'anime') displayedItems = items.filter(i => i.type === 'anime');

  return (
    <div className="min-h-screen bg-background text-white pb-24 relative">
      {/* Header */}
      <header className="glass-panel sticky top-0 z-40 rounded-none border-t-0 border-l-0 border-r-0 border-white/10 p-4 px-6 md:px-12 flex justify-between items-center bg-black/50">
        <div className="flex items-center gap-3">
          <Tv className="w-8 h-8 text-[#00f2fe]" />
          <h1 className="text-2xl font-bold neon-text-primary tracking-widest hidden md:block">WATCHVIBE</h1>
        </div>
        
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2 border border-white/20 rounded-full px-4 py-2 bg-white/5">
            <img src={user?.avatarUrl} alt={user?.username} className="w-8 h-8 rounded-full border border-primary shadow-neon-primary" />
            <span className="font-bold text-sm hidden sm:block">{user?.username}</span>
          </div>
          <button onClick={logout} className="text-textMuted hover:text-neonRed transition-colors" title="Switch Profile">
            <LogOut className="w-6 h-6" />
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mt-8 space-y-12">
        {/* Smart Dashboard Stats */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="glass-card p-6 border-l-4 border-l-primary flex items-center justify-between">
            <div>
              <p className="text-textMuted text-sm tracking-widest uppercase mb-1">Total Vault</p>
              <h2 className="text-4xl font-bold">{totalItems}</h2>
            </div>
            <Tv className="w-12 h-12 text-primary opacity-50" />
          </div>
          <div className="glass-card p-6 border-l-4 border-l-neonYellow flex items-center justify-between">
            <div>
              <p className="text-textMuted text-sm tracking-widest uppercase mb-1">Currently Watching</p>
              <h2 className="text-4xl font-bold">{watchingItems.length}</h2>
            </div>
            <Play className="w-12 h-12 text-neonYellow opacity-50" />
          </div>
          <div className="glass-card p-6 border-l-4 border-l-neonGreen flex flex-col justify-center relative overflow-hidden">
            <div className="flex justify-between items-end mb-2 relative z-10">
              <div>
                <p className="text-textMuted text-sm tracking-widest uppercase mb-1">Completed</p>
                <h2 className="text-4xl font-bold">{completedItems.length}</h2>
              </div>
              <span className="text-neonGreen font-bold text-xl">{completionPercent}%</span>
            </div>
            <div className="w-full bg-white/10 h-2 rounded-full relative z-10 overflow-hidden">
              <motion.div 
                initial={{ width: 0 }}
                animate={{ width: `${completionPercent}%` }}
                transition={{ duration: 1, ease: "easeOut" }}
                className="h-full bg-neonGreen shadow-neon-green"
              />
            </div>
            <CheckCircle className="w-24 h-24 text-neonGreen opacity-10 absolute -right-4 -bottom-4" />
          </div>
        </section>

        {/* Currently Watching Horizontal Scroll */}
        {watchingItems.length > 0 && (
          <section>
            <h2 className="text-2xl font-bold mb-4 flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-neonYellow animate-pulse-glow"></span>
              Live Activity
            </h2>
            <div className="flex gap-6 overflow-x-auto pb-6 custom-scrollbar snap-x snap-mandatory pr-8">
              {watchingItems.map(item => (
                <div key={item._id} className="min-w-[280px] max-w-[280px] snap-center shrink-0">
                  <MediaCard 
                    item={item} 
                    profiles={profiles} 
                    currentUser={user} 
                    onUpdate={handleItemUpdate} 
                  />
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Main Watchlist */}
        <section>
          <div className="flex flex-col sm:flex-row justify-between items-center mb-6 gap-4">
            <h2 className="text-2xl font-bold border-b-2 border-primary pb-1 inline-block">The Watchlist</h2>
            
            {/* Filter Tabs */}
            <div className="flex bg-white/5 p-1 rounded-full border border-white/10">
              {['all', 'movies', 'series', 'anime'].map(tab => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-4 py-2 rounded-full text-sm font-medium transition-all ${activeTab === tab ? 'bg-white/10 text-primary shadow-neon-primary' : 'text-textMuted hover:text-white hover:bg-white/5'}`}
                >
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
            <AnimatePresence>
              {displayedItems.map(item => (
                <motion.div
                  key={item._id}
                  layout
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.9 }}
                  transition={{ duration: 0.3 }}
                >
                  <MediaCard 
                    item={item} 
                    profiles={profiles} 
                    currentUser={user} 
                    onUpdate={handleItemUpdate} 
                  />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
          
          {displayedItems.length === 0 && (
            <div className="py-20 text-center glass-panel border-dashed">
              <Tv className="w-16 h-16 text-textMuted mx-auto mb-4 opacity-50" />
              <h3 className="text-xl font-bold text-textMuted mb-2">It's quiet here...</h3>
              <p className="text-white/50">Time to add some entertainment to the vibe.</p>
            </div>
          )}
        </section>
      </main>

      {/* Floating Add Button */}
      <button 
        onClick={() => setIsAddModalOpen(true)}
        className="fixed bottom-8 right-8 w-16 h-16 rounded-full bg-gradient-to-r from-primary to-neonGreen flex items-center justify-center text-black shadow-neon-primary hover:scale-110 transition-transform z-50"
      >
        <Plus className="w-8 h-8 font-bold" />
      </button>

      <AddMediaModal 
        isOpen={isAddModalOpen} 
        onClose={() => setIsAddModalOpen(false)} 
        onAdd={handleItemAdd} 
      />
    </div>
  );
};

export default Dashboard;
