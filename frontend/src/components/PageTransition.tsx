import React from 'react';
import { motion } from 'framer-motion';

interface PageTransitionProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * PageTransition: Cinematic fade and slide transitions for page changes
 * Uses Framer Motion for smooth animations
 */
export default function PageTransition({ children, className = '' }: PageTransitionProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{
        type: 'spring',
        stiffness: 260,
        damping: 20,
        duration: 0.3
      }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
