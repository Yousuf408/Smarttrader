// ================================================================
// SETTINGS PAGE – Broker Configuration
// ================================================================
function toggleBrokerFields() {
    const broker = document.getElementById('brokerSelect').value;
    const dhan = document.getElementById('dhanFields');
    const other = document.getElementById('otherBrokerFields');
    
    if (broker === 'dhan') {
        dhan.style.display = 'grid';
        other.style.display = 'none';
    } else if (broker) {
        dhan.style.display = 'none';
        other.style.display = 'block';
    } else {
        dhan.style.display = 'none';
        other.style.display = 'none';
    }
}
