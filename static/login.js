'use strict';
(async () => {
  const message = document.getElementById('login-message');
  try {
    const response = await fetch('/api/auth/config', {credentials:'same-origin', cache:'no-store'});
    const config = await response.json();
    if (!response.ok) throw new Error(config.error);
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.onerror = () => {message.textContent = 'Google sign-in could not load. Check your connection and refresh.';};
    script.onload = () => {
      google.accounts.id.initialize({client_id:config.clientId, nonce:config.nonce, auto_select:false,
        callback: async result => {
          message.textContent = 'Opening your space…';
          try {
            const response = await fetch('/api/auth/google', {method:'POST', credentials:'same-origin',
              headers:{'Content-Type':'application/json', 'X-Student-Request':'1'},
              body:JSON.stringify({credential:result.credential})});
            const body = await response.json();
            if (!response.ok) throw new Error(body.error);
            location.assign(body.redirect === '/parent' ? '/parent' : '/student');
          } catch (error) {
            message.textContent = error.message + ' Refresh this page to try again.';
          }
        }});
      google.accounts.id.renderButton(document.getElementById('google-signin'), {theme:'outline',size:'large',shape:'pill',text:'signin_with'});
      message.textContent = '';
    };
    document.head.append(script);
  } catch (error) {message.textContent = error.message || 'Sign-in is not ready yet. Please try again.';}
})();
