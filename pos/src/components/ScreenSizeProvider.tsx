import { useState, useEffect } from 'react';
import ScreenSizeDialog from './ScreenSizeDialog';
import { usePOSStore } from '../store/pos-store';

interface ScreenSizeProviderProps {
  children: React.ReactNode;
}

const ScreenSizeProvider = ({ children }: ScreenSizeProviderProps) => {
  const [isScreenTooSmall, setIsScreenTooSmall] = useState(false);
  const posProfile = usePOSStore((state) => state.posProfile);

  const checkScreenSize = () => {
    const isSmall = window.innerWidth < 1024;
    setIsScreenTooSmall(isSmall);
  };

  useEffect(() => {
    // Check on mount
    checkScreenSize();

    // Add resize listener
    const handleResize = () => {
      checkScreenSize();
    };

    window.addEventListener('resize', handleResize);

    // Cleanup
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, []);

  // Show dialog if screen is too small
  if (isScreenTooSmall) {
    return (
      <ScreenSizeDialog
        profileReady={!!posProfile}
        allowLegacyPOS={!!posProfile && !posProfile.custom_ury_enable_commercial_checkout}
      />
    );
  }

  // Render children if screen size is acceptable
  return <>{children}</>;
};

export default ScreenSizeProvider;
