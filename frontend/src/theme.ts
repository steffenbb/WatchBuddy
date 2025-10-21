/**
 * Shared theme configuration for WatchBuddy UI
 * Based on MetadataBuildProgress and Setup components
 */

export const theme = {
  // Color palette
  colors: {
    // Primary dark gradient (used for backgrounds)
    gradientDark: 'from-indigo-900 via-purple-900 to-pink-900',
    gradientLight: 'from-fuchsia-100 via-indigo-50 to-blue-100',
    
    // Glass morphism
    glass: {
      bg: 'bg-white/10',
      border: 'border-white/20',
      hover: 'hover:bg-white/15',
      backdrop: 'backdrop-blur-lg',
    },
    
    // Accents
    primary: {
      base: 'indigo-600',
      light: 'indigo-500',
      dark: 'indigo-700',
      hover: 'hover:indigo-700',
    },
    
    secondary: {
      base: 'purple-600',
      light: 'purple-500',
      dark: 'purple-700',
    },
    
    accent: {
      pink: 'pink-500',
      fuchsia: 'fuchsia-500',
    },
    
    // Status colors
    success: 'emerald-500',
    warning: 'amber-500',
    error: 'red-500',
    info: 'blue-500',
    
    // Text
    text: {
      primary: 'text-white',
      secondary: 'text-white/80',
      muted: 'text-white/60',
      dark: 'text-gray-900',
      darkSecondary: 'text-gray-700',
    },
  },
  
  // Spacing for touch-friendly UI
  spacing: {
    touchTarget: '44px', // Minimum tap target size
    cardPadding: 'p-6 md:p-8',
    sectionGap: 'gap-6 md:gap-8',
  },
  
  // Border radius
  radius: {
    card: 'rounded-2xl md:rounded-3xl',
    button: 'rounded-xl',
    badge: 'rounded-full',
    input: 'rounded-lg',
  },
  
  // Shadows
  shadow: {
    card: 'shadow-2xl',
    button: 'shadow-lg',
    hover: 'hover:shadow-xl',
  },
  
  // Transitions
  transition: {
    base: 'transition-all duration-200',
    slow: 'transition-all duration-300',
    colors: 'transition-colors duration-200',
  },
  
  // Typography
  typography: {
    heading1: 'text-4xl md:text-5xl font-bold',
    heading2: 'text-3xl md:text-4xl font-bold',
    heading3: 'text-2xl md:text-3xl font-semibold',
    heading4: 'text-xl md:text-2xl font-semibold',
    body: 'text-base md:text-lg',
    small: 'text-sm',
    tiny: 'text-xs',
  },
};

// Helper functions to build class strings
export const glassmorphic = () =>
  `${theme.colors.glass.bg} ${theme.colors.glass.backdrop} ${theme.colors.glass.border} border`;

export const card = () =>
  `${glassmorphic()} ${theme.radius.card} ${theme.shadow.card} ${theme.spacing.cardPadding}`;

export const button = (variant: 'primary' | 'secondary' | 'glass' = 'primary') => {
  const base = `${theme.radius.button} ${theme.shadow.button} ${theme.transition.colors} font-semibold px-6 py-3 min-h-[44px]`;
  
  if (variant === 'primary') {
    return `${base} bg-${theme.colors.primary.base} text-white hover:bg-${theme.colors.primary.dark}`;
  }
  if (variant === 'secondary') {
    return `${base} bg-${theme.colors.secondary.base} text-white hover:bg-${theme.colors.secondary.dark}`;
  }
  if (variant === 'glass') {
    return `${base} ${glassmorphic()} text-white ${theme.colors.glass.hover}`;
  }
  return base;
};

export const badge = (color: 'primary' | 'secondary' | 'success' | 'warning' = 'primary') => {
  const base = `${theme.radius.badge} px-3 py-1 text-sm font-medium`;
  const colorMap = {
    primary: `bg-${theme.colors.primary.base} text-white`,
    secondary: `bg-${theme.colors.secondary.base} text-white`,
    success: `bg-${theme.colors.success} text-white`,
    warning: `bg-${theme.colors.warning} text-white`,
  };
  return `${base} ${colorMap[color]}`;
};
