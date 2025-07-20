$(function() {
    // Initialize particles.js with deep purple theme
    particlesJS("particles-js", {
      "particles": {
        "number": { 
          "value": 90, 
          "density": { 
            "enable": true, 
            "value_area": 900 
          } 
        },
        "color": { 
          "value": "#d1c4e9" 
        },
        "shape": { 
          "type": "circle", 
          "stroke": { 
            "width": 0, 
            "color": "#000000" 
          } 
        },
        "opacity": { 
          "value": 0.7, 
          "random": true 
        },
        "size": { 
          "value": 4, 
          "random": true 
        },
        "line_linked": { 
          "enable": true, 
          "distance": 180, 
          "color": "#b39ddb", 
          "opacity": 0.4, 
          "width": 1 
        },
        "move": { 
          "enable": true, 
          "speed": 2.5, 
          "direction": "none", 
          "random": true, 
          "straight": false, 
          "out_mode": "out" 
        }
      },
      "interactivity": {
        "detect_on": "canvas",
        "events": {
          "onhover": { 
            "enable": true, 
            "mode": "repulse" 
          },
          "onclick": { 
            "enable": true, 
            "mode": "push" 
          },
          "resize": true
        },
        "modes": { 
          "repulse": { 
            "distance": 100, 
            "duration": 0.4 
          },
          "push": {
            "particles_nb": 4
          }
        }
      }
    });
  
    const $body = $('#chat-body');
    const $input = $('#user-input');
    const $btn = $('#send-btn');
    
    // Welcome animation
    setTimeout(() => {
      $('.welcome-message h3').addClass('animate__pulse');
    }, 1500);
  
    function scrollBottom() {
      $body.stop().animate({ scrollTop: $body[0].scrollHeight }, 500);
    }
  
    function appendMessage(text, sender) {
      const cls = sender === 'user' ? 'user' : 'bot';
      const $msg = $(`
        <div class="message ${cls} animate__animated animate__fadeInUp">
          ${sender === 'bot' ? marked.parse(text) : text}
          <div class="message-actions">
            ${sender === 'bot' ? `<button class="speak-btn" title="Listen"><i class="fas fa-volume-up"></i></button>` : ''}
            ${sender === 'bot' ? `<button class="download-btn" title="Download"><i class="fas fa-download"></i></button>` : ''}
          </div>
        </div>
      `);
      $body.append($msg);
      
      // Highlight dates in bot responses
      if (sender === 'bot') {
        $msg.html($msg.html().replace(/(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}/g, 
          '<span class="date-highlight">$&</span>'));
      }
      
      // Attach event handlers to new buttons
      if (sender === 'bot') {
        $msg.find('.speak-btn').on('click', function() {
          speakMessage(text);
        });
        
        $msg.find('.download-btn').on('click', function() {
          downloadMessage(text);
        });
      }
      
      scrollBottom();
    }
  
    function speakMessage(text) {
      $.ajax({
        url: '/speak',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ 
          text: text,
          lang: 'en'
        }),
        success: function() {
          Swal.fire({
            title: 'Speaking Response',
            text: 'Now playing the advisor response',
            icon: 'info',
            timer: 2000,
            timerProgressBar: true,
            toast: true,
            position: 'top-end',
            showConfirmButton: false
          });
        },
        error: function() {
          Swal.fire({
            title: 'Audio Error',
            text: 'Could not play the response',
            icon: 'error',
            timer: 2000,
            timerProgressBar: true,
            toast: true,
            position: 'top-end',
            showConfirmButton: false
          });
        }
      });
    }
  
    function downloadMessage(text) {
      $.ajax({
        url: '/download',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ 
          text: text,
          lang: 'en'
        }),
        success: function(response) {
          if (response.status === 'success') {
            // Create invisible link and trigger click
            const a = document.createElement('a');
            a.href = response.url;
            a.download = `edupath_advice_${new Date().toISOString().slice(0,10)}.txt`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
          }
        },
        error: function() {
          Swal.fire({
            title: 'Download Error',
            text: 'Could not download the response',
            icon: 'error',
            timer: 2000,
            timerProgressBar: true,
            toast: true,
            position: 'top-end',
            showConfirmButton: false
          });
        }
      });
    }
  
    function showLoader() {
      const $loader = $('<div class="message bot loader animate__animated animate__fadeIn"></div>');
      for (let i = 0; i < 3; i++) {
        $loader.append('<div></div>');
      }
      $body.append($loader);
      scrollBottom();
      return $loader;
    }
  
    function sendQuery() {
      const query = $input.val().trim();
      if (!query) return;
      
      appendMessage(query, 'user');
      $input.val('');
      
      const $loader = showLoader();
  
      $.ajax({
        url: '/ask',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ 
          query: query
        }),
        success: function(response) {
          $loader.remove();
          appendMessage(response.response, 'bot');
        },
        error: function() {
          $loader.remove();
          appendMessage('⚠️ Error connecting to the admissions database. Please try again.', 'bot');
        }
      });
    }
  
    // Event handlers
    $btn.on('click', sendQuery);
    $input.on('keydown', function(e) { 
      if (e.key === 'Enter') sendQuery(); 
    });
    
    // Auto-focus input with animation
    setTimeout(() => {
      $input.focus();
      $input.addClass('animate__animated animate__pulse');
      setTimeout(() => {
        $input.removeClass('animate__animated animate__pulse');
      }, 1000);
    }, 2000);
    
    // Floating caps animation
    setInterval(() => {
      $('.degree-cap').each(function() {
        $(this).css({
          'transform': `translateY(${Math.sin(Date.now()/1000 + parseInt($(this).css('animation-delay'))*2)*15}px) rotate(${Math.sin(Date.now()/800 + parseInt($(this).css('animation-delay'))*3)*10}deg)`
        });
      });
    }, 50);
  });